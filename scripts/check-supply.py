#!/usr/bin/env python3
"""Semantic check of Liqwid V2 ADA supplies against recorded fixtures.

Decodes each fixture transaction against the recorded market snapshot,
derives the qADA a supply must mint from the market state's exchange pair,
asserts the expected effects, the exact outputs and the script data hash,
requires every negative fixture to fail with its named reason, and checks
that the built TIR is the one the generated fixture came from and that the
exported TII, profile, invoke args and README use the same names. Standard
library only.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "liqwid-v2"
FIXTURES = PROTOCOL / "fixtures"
# Ledger language-view keys. PlutusV1 (key 0) encodes its view differently
# and is not supported here.
VIEW_KEY = {"PlutusV2": 1, "PlutusV3": 2}
WITNESS_SCRIPT_KEY = {3: "PlutusV1", 6: "PlutusV2", 7: "PlutusV3"}


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
        keys = [tuple(k) if isinstance(k, list) else k for k in items[0::2]]
        return dict(zip(keys, items[1::2])), pos
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


def cbor_head(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    size = next(s for s in (1, 2, 4, 8) if n < 1 << (8 * s))
    return bytes([major << 5 | {1: 24, 2: 25, 4: 26, 8: 27}[size]]) + n.to_bytes(size, "big")


def language_views(cost_models):
    """CBOR of the ledger's language views for PlutusV2/V3 cost models."""
    out = cbor_head(5, len(cost_models))
    for lang in sorted(cost_models, key=VIEW_KEY.get):
        costs = cost_models[lang]
        out += cbor_head(0, VIEW_KEY[lang]) + cbor_head(4, len(costs))
        out += b"".join(cbor_head(0, c) if c >= 0 else cbor_head(1, -1 - c) for c in costs)
    return out


def script_hash(language, script):
    prefix = {"PlutusV1": 1, "PlutusV2": 2, "PlutusV3": 3}[language]
    return hashlib.blake2b(bytes([prefix]) + script, digest_size=28).hexdigest()


def utxo_ref(ref):
    txid, index = ref.split("#")
    return txid, int(index)


def decode_tx(tx_cbor_hex):
    """Return the parts of a Conway transaction body a supply is judged on."""
    raw = bytes.fromhex(tx_cbor_hex)
    body, body_end = cbor_decode(raw, 1)
    # Witness set: keep the raw redeemer and datum bytes the script data hash covers.
    witness_raw, pos = {}, body_end + 1
    for _ in range(raw[body_end] & 0x1F):
        key, pos = cbor_decode(raw, pos)
        start = pos
        _, pos = cbor_decode(raw, pos)
        witness_raw[key] = raw[start:pos]
    scripts = {}
    for key, lang in WITNESS_SCRIPT_KEY.items():
        for script in cbor_decode(witness_raw[key])[0] if key in witness_raw else []:
            scripts[script_hash(lang, script)] = lang
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
        "witness_scripts": scripts,
        "script_data": witness_raw.get(5, b"") + witness_raw.get(4, b""),
        "script_data_hash": body[11].hex() if 11 in body else None,
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
    wanted_refs = {utxo_ref(hist[k]["ref"]) for k in ("market_state", "market_params", "action_script")}
    need(wanted_refs <= tx["references"], "references")
    # The qADA policy runs from its reference script or as an inline witness.
    qada_ref = utxo_ref(hist["qada_script"]["ref"]) in tx["references"]
    need(tx["witness_scripts"] == ({} if qada_ref else {qada["policy"]: qada["script_language"]}), "scripts")

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

    # Recipient: the minted qADA goes to the supplier's address, whose key signs.
    holders = [o for o in tx["outputs"] if qada["policy"] in o["assets"]]
    need(len(holders) == 1, "receipt-recipient")
    receipt = holders[0]
    need(receipt["assets"][qada["policy"]] == {qada["asset_name"]: qada_due}, "qada-quantity")
    need(receipt["address"] == expected["supplier_address_hex"], "receipt-recipient")
    need(receipt["address"][2:58] == expected["supplier_pkh"], "receipt-recipient")
    need(expected["supplier_pkh"] in tx["signers"], "signer")

    # Outputs: the successor and the receipt, nothing else.
    need(len(tx["outputs"]) == 2, "outputs")

    # Script data hash: redeemers and the language views of the scripts that
    # run (both PlutusV2), with the cost models of the recorded epoch.
    params = snapshot["protocol_parameters"]
    languages = {action["script_language"], qada["script_language"]}
    views = language_views({lang: params["cost_models"][lang] for lang in languages})
    want = hashlib.blake2b(tx["script_data"] + views, digest_size=32).hexdigest()
    need(tx["script_data_hash"] == want, "script-data-hash")

    return {"supplied_lovelace": supplied, "qada_minted": minted, "qada_policy": qada["policy"],
            "exchange": exchange, "action_datum": successor["datum"],
            "receipt_address": receipt["address"], "fee": tx["fee"],
            "change_lovelace": receipt["lovelace"]}


def tii_path():
    manifest = dict(re.findall(r'^(scope|name|version) = "([^"]+)"', (PROTOCOL / "trix.toml").read_text(), re.M))
    return PROTOCOL / ".tx3/tii" / manifest["scope"] / manifest["name"] / manifest["version"] / "main.tii"


def check_interface(deployment, generated):
    tii = json.loads(tii_path().read_text())
    tx = tii["transactions"]["supply_ada"]
    tir = hashlib.sha256(tx["tir"]["content"].encode()).hexdigest()
    assert tir == generated["tir_sha256"], (
        f"built TIR {tir} != {generated['tir_sha256']}: main.tx3 changed supply_ada, "
        "regenerate fixtures/generated-supply.json")
    params = set(tx["params"]["properties"])
    profile = tii["profiles"]["preview"]
    env = dict(profile["environment"])
    qada_script = bytes.fromhex(env.pop("qada_script").removeprefix("0x"))
    assert script_hash(deployment["qada"]["script_language"], qada_script) == deployment["qada"]["policy"], \
        "preview qada_script does not hash to the qADA policy"
    want = {"qada_policy": deployment["qada"]["policy"],
            "action_script_ref": deployment["action"]["script_ref"],
            "market_params_ref": deployment["market_params"]["ref"]}
    assert env == want, f"preview profile env {env} != {want}"
    env = {**want, "qada_script": None}
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
    fixtures = {name: json.loads((FIXTURES / f"{name}-supply.json").read_text()) for name in ("reference", "generated")}
    args = check_interface(deployment, fixtures["generated"])
    print("ok   interface: built TIR matches the generated fixture; TII, profile, invoke args and README agree")
    hist = snapshot["historical"]
    assert args["action_utxo"] == hist["action"]["ref"], "invoke args action_utxo != snapshot"
    assert args["market_state_utxo"] == hist["market_state"]["ref"], "invoke args market_state_utxo != snapshot"
    exchange = cbor_hex(hist["market_state"]["datum_cbor"])[deployment["market_state"]["exchange_field"]]
    assert [args["exchange_lovelace"], args["exchange_qada"]] == exchange, "invoke args exchange pair != market state"
    print("ok   invoke args replay the snapshot's action, market state and exchange pair")
    decoded = {}
    for name, fx in fixtures.items():
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
