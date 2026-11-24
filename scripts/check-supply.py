#!/usr/bin/env python3
"""Semantic check of Liqwid V2 ADA supplies against recorded fixtures.

Decodes each fixture transaction against the recorded market snapshot,
derives the qADA a supply must mint from the market state's exchange pair,
asserts the expected effects, requires every negative fixture to fail with
its named reason, and checks that the exported TII, profile, invoke args and
README use the same names. Standard library only.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "liqwid-v2"
FIXTURES = PROTOCOL / "fixtures"
TII = PROTOCOL / ".tx3/tii/open-tx3/liqwid-v2/0.1.0/main.tii"


class Tag:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


def cbor_decode(data, pos=0):
    """Decode one CBOR item; returns (value, next_pos)."""
    head = data[pos]
    major, info = head >> 5, head & 0x1F
    pos += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        arg = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    elif info == 31:
        arg = None
    else:
        raise ValueError(f"unsupported CBOR header {head:#x}")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if arg is None:
            chunks = []
            while data[pos] != 0xFF:
                chunk, pos = cbor_decode(data, pos)
                chunks.append(chunk)
            joined = b"".join(chunks) if major == 2 else "".join(chunks)
            return joined, pos + 1
        raw = data[pos:pos + arg]
        return (raw if major == 2 else raw.decode()), pos + arg
    if major in (4, 5):
        items = []
        count = 0
        while (arg is None and data[pos] != 0xFF) or (arg is not None and count < arg * (major - 3)):
            item, pos = cbor_decode(data, pos)
            items.append(item)
            count += 1
        if arg is None:
            pos += 1
        if major == 4:
            return items, pos
        return dict(zip(items[0::2], items[1::2])), pos
    if major == 6:
        value, pos = cbor_decode(data, pos)
        if arg == 2:
            return int.from_bytes(value, "big"), pos
        if arg == 3:
            return -1 - int.from_bytes(value, "big"), pos
        if arg == 258:
            return value, pos
        return Tag(arg, value), pos
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], pos
    raise ValueError(f"unsupported CBOR major type {major}")


def cbor_hex(hex_str):
    return cbor_decode(bytes.fromhex(hex_str))[0]


def utxo_ref(ref):
    txid, index = ref.split("#")
    return txid, int(index)


def decode_tx(tx_cbor_hex):
    """Return the parts of a Conway transaction body a supply is judged on."""
    raw = bytes.fromhex(tx_cbor_hex)
    _, body_end = cbor_decode(raw, 1)
    body = cbor_decode(raw, 1)[0]
    outputs = []
    for out in body[1]:
        if isinstance(out, dict):
            address, value, datum_option = out[0], out[1], out.get(2)
        else:
            address, value = out[0], out[1]
            datum_option = None
        datum = None
        if datum_option is not None:
            if datum_option[0] != 1:
                raise ValueError("datum")
            datum = cbor_decode(datum_option[1].value)[0]
        coin, assets = (value, {}) if isinstance(value, int) else (value[0], value[1])
        outputs.append({"address": address.hex(), "lovelace": coin,
                        "assets": {p.hex(): {n.hex(): q for n, q in m.items()} for p, m in assets.items()},
                        "datum": datum})
    return {
        "hash": hashlib.blake2b(raw[1:body_end], digest_size=32).hexdigest(),
        "inputs": {(i[0].hex(), i[1]) for i in body[0]},
        "outputs": outputs,
        "fee": body[2],
        "mint": {p.hex(): {n.hex(): q for n, q in m.items()} for p, m in body.get(9, {}).items()},
        "signers": {s.hex() for s in body.get(14, [])},
        "references": {(i[0].hex(), i[1]) for i in body.get(18, [])},
    }


def supply_effects(tx, deployment, snapshot, expected):
    """Decode one supply and check it; raises ValueError(reason)."""
    hist = snapshot["historical"]
    action = deployment["action"]
    qada = deployment["qada"]

    def need(cond, reason):
        if not cond:
            raise ValueError(reason)

    # Market: spends the snapshot's ADA action UTxO and returns it to the
    # same validator address with the ADA market's action token.
    need(utxo_ref(hist["action"]["ref"]) in tx["inputs"], "market")
    token = {action["token_policy"]: {action["token_name"]: 1}}
    successors = [o for o in tx["outputs"] if o["address"] == action["address_hex"]]
    need(len(successors) == 1 and successors[0]["assets"] == token, "market")
    successor = successors[0]
    wanted_refs = {utxo_ref(hist[k]["ref"]) for k in ("market_state", "market_params", "action_script", "qada_script")}
    need(wanted_refs <= tx["references"], "references")

    # Deposit: the successor holds the prior ADA plus the supply.
    supplied = successor["lovelace"] - hist["action"]["lovelace"]
    need(supplied == expected["supply_lovelace"], "deposit")

    # Receipt: qADA from the exchange pair in the market state datum.
    exchange = cbor_hex(hist["market_state"]["datum_cbor"])[deployment["market_state"]["exchange_field"]]
    qada_due = supplied * exchange[1] // exchange[0]
    minted = tx["mint"].get(qada["policy"], {}).get(qada["asset_name"])
    need(qada_due == expected["qada_minted"], "qada-quantity")
    need(tx["mint"] == {qada["policy"]: {qada["asset_name"]: qada_due}}, "qada-quantity")
    totals, marker = cbor_hex(hist["action"]["datum_cbor"])
    need(successor["datum"] == [[totals[0] + supplied, totals[1] + minted] + totals[2:], marker], "qada-quantity")

    # Recipient: the minted qADA goes to the supplier's key, who signs.
    holders = [o for o in tx["outputs"] if qada["policy"] in o["assets"]]
    need(len(holders) == 1, "receipt-recipient")
    receipt = holders[0]
    need(receipt["assets"][qada["policy"]] == {qada["asset_name"]: qada_due}, "qada-quantity")
    # Shelley header types 0, 2, 4 and 6 carry a key payment credential.
    need(int(receipt["address"][:2], 16) >> 4 in (0, 2, 4, 6), "receipt-recipient")
    need(receipt["address"][2:58] == expected["supplier_pkh"], "receipt-recipient")
    need(expected["supplier_pkh"] in tx["signers"], "signer")

    return {"supplied_lovelace": supplied, "qada_minted": minted, "qada_policy": qada["policy"],
            "exchange": exchange, "action_datum": successor["datum"],
            "receipt_address": receipt["address"], "fee": tx["fee"],
            "change_lovelace": receipt["lovelace"]}


def check_interface(deployment):
    tii = json.loads(TII.read_text())
    tx = tii["transactions"]["supply_ada"]
    params = set(tx["params"]["properties"])
    profile = tii["profiles"]["preview"]
    env = {"qada_policy": deployment["qada"]["policy"],
           "qada_script_ref": deployment["qada"]["script_ref"],
           "action_script_ref": deployment["action"]["script_ref"],
           "market_params_ref": deployment["market_params"]["ref"]}
    assert profile["environment"] == env, f"preview profile env {profile['environment']} != {env}"
    assert profile["parties"]["actionscript"] == deployment["action"]["address"], "preview ActionScript"
    args = json.loads((PROTOCOL / "invoke-args/supply_ada.json").read_text())
    names = params | set(tii["parties"])
    assert set(args) <= names, f"invoke args not in interface: {set(args) - names}"
    assert set(tx["params"]["required"]) <= set(args), "invoke args miss a required param"
    readme = (PROTOCOL / "README.md").read_text()
    missing = [n for n in params | set(env) | {"supply_ada", "preview"} if f"`{n}`" not in readme]
    assert not missing, f"README does not document {missing}"
    return args


def main():
    deployment = json.loads((FIXTURES / "deployment.json").read_text())
    snapshot = json.loads((FIXTURES / "market-snapshot.json").read_text())
    args = check_interface(deployment)
    print("ok   interface: TII, preview profile, invoke args and README agree")
    hist = snapshot["historical"]
    assert args["action_utxo"] == hist["action"]["ref"], "invoke args action_utxo != snapshot"
    assert args["market_state_utxo"] == hist["market_state"]["ref"], "invoke args market_state_utxo != snapshot"
    exchange = cbor_hex(hist["market_state"]["datum_cbor"])[deployment["market_state"]["exchange_field"]]
    assert [args["exchange_lovelace"], args["exchange_qada"]] == exchange, "invoke args exchange pair != market state"
    print("ok   invoke args replay the snapshot's action, market state and exchange pair")
    decoded = {}
    for name in ("reference", "generated"):
        fx = json.loads((FIXTURES / f"{name}-supply.json").read_text())
        tx = decode_tx(fx["tx_cbor"])
        assert tx["hash"] == fx["tx_hash"], f"{name}: body hash {tx['hash']} != {fx['tx_hash']}"
        decoded[name] = supply_effects(tx, deployment, snapshot, fx["expected"])
        print(f"ok   {name}: {fx['tx_hash']} matches its expected effects")
    same = ("supplied_lovelace", "qada_minted", "qada_policy", "exchange", "action_datum", "receipt_address")
    diff = [k for k in same if decoded["reference"][k] != decoded["generated"][k]]
    assert not diff, f"generated supply differs from the reference in {diff}"
    print("ok   generated supply decodes to the same effects as the reference")
    for name, d in decoded.items():
        print(f"     {name}: fee {d['fee']}, change {d['change_lovelace']} lovelace (not part of the deposit)")
    failures = 0
    for path in sorted((FIXTURES / "negative").glob("*.json")):
        fx = json.loads(path.read_text())
        try:
            supply_effects(decode_tx(fx["tx_cbor"]), deployment, snapshot, fx["expected"])
            reason = None
        except ValueError as err:
            reason = str(err)
        if reason == fx["rejects"]:
            print(f"ok   negative/{path.name}: rejected ({reason})")
        else:
            failures += 1
            print(f"FAIL negative/{path.name}: expected rejection '{fx['rejects']}', got {reason!r}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
