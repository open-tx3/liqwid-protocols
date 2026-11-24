# Liqwid V2 — supply ADA

[Liqwid](https://liqwid.finance/) is a lending protocol on Cardano. Suppliers
deposit an asset into a market and receive **qTokens**, receipt tokens
redeemable for the asset plus accrued interest
([supply and qADA guide](https://docs.liqwid.finance/docs/user-guide/)).

This interface covers one operation on the **ADA market** on **preview**:
`supply_ada` deposits ADA and mints qADA to the supplier. Withdraw, borrow,
other markets and the batch that folds deposits into the market state are out
of scope.

Liqwid does not publish its V2 validator source. The reference is the owner's
public supply builder
([API documentation](https://liqwid-labs.gitbook.io/liqwid-docs/api-documentation),
`liqwid.transactions.supply` at `https://v2.api.preview.liqwid.dev/graphql`)
and the deployed scripts, which the transaction this interface builds passes
on preview.
Tagged release source: [`v0.1.0`](https://github.com/open-tx3/liqwid-protocols/tree/v0.1.0/liqwid-v2)
(the tag is created when the version is published).

## Operation

| Transaction | Network | What it does |
|---|---|---|
| `supply_ada` | preview | Spends one ADA market action UTxO, returns it with `supply_lovelace` more ADA and the deposit added to its datum, and mints qADA to the supplier. |

The supplier pays, signs (`supplier_pkh` is a required signer) and posts the
collateral. Both scripts are PlutusV2 and take the unit redeemer
`Constr 0 []`. The action validator runs from its reference script. The qADA
policy is attached inline (`qada_script`): the transaction resolver cannot read
a reference script's language, and only an inline PlutusV2 script gives the
script data hash the PlutusV2 language view the ledger requires.

## Deployment (`preview` profile)

| Item | Value |
|---|---|
| Market | `Ada` (owner API market id) |
| `ActionScript` party | `addr_test1zrdr65772au040dcghd0u4w9lagydrk8th6m30e9caenw3atry0x5jr70sy0c55u0n0xjau2yfpegfmwpnvrpjx9spwsrt7c6x` (validator `da3d53de…3747`) |
| Action token | `af829f4472c3728307007831fa000e450883cfca4893d9ef535e8786`, empty name, one per action UTxO (four exist) |
| `qada_policy` | `50e015ec8204db83a4f57aa9ee40ce6ea157e3b7335a149fafe3f370`, empty asset name, 6 decimals |
| `action_script_ref` | `04dd9fbaa2305b1309843542821c580c724b4bec518d6d6280ec9ac6cdc6f0c9#0` (PlutusV2) |
| `qada_script` | the qADA policy's PlutusV2 script (4 991 bytes), copied from the reference script at `dbb5d1c10ed2cd5a5105c1f50ce30dbdac7707060e3eb1be7034cdbce623ccc2#0`; it hashes to `qada_policy` |
| `market_params_ref` | `344b090d588a045d4e903c6cafe3d6e18bc349faac5f07b442cc63db0c81702c#0` |
| Market state | UTxO holding token `b9a05feafb9a147460f8c9e6f63de66d5f3d151ddcd93d0132e90fb6` at `addr_test1wrdk050fs7r9537m9wxfnqzmjsxnmud5v8le6vjh6k5jhvchuyj76` |

Every coordinate and where it was read is in
[`fixtures/deployment.json`](fixtures/deployment.json); the UTxOs as they stood
for the recorded transactions are in
[`fixtures/market-snapshot.json`](fixtures/market-snapshot.json).

## Inputs

| Name | Type | Unit | Meaning |
|---|---|---|---|
| `supplier` | party | bech32 address | Wallet that pays, receives the qADA and change. |
| `supplier_pkh` | Bytes | 28-byte hex | Payment key hash of `supplier`; the required signer. |
| `supply_lovelace` | Int | lovelace (1 ADA = 1 000 000) | ADA deposited into the market. |
| `action_utxo` | UtxoRef | `txid#index` | Any current UTxO at `ActionScript` holding the action token. |
| `market_state_utxo` | UtxoRef | `txid#index` | Current UTxO holding the market state token. |
| `exchange_lovelace` | Int | lovelace | Element 0 of field 9 of the `market_state_utxo` datum: underlying ADA. |
| `exchange_qada` | Int | qADA smallest unit | Element 1 of the same field: qADA supply. |

Every batch replaces the action and market state UTxOs and moves the exchange
pair, so read all four live inputs from the chain just before building. The
exchange pair is an input rather than read from the reference datum because
the current transaction resolver fails on that datum's big integers.

**Read the pair from the datum of the `market_state_utxo` you pass.** That
datum is a plain list; field 9 (counting from 0) is the two-element list
`[underlying lovelace, qADA supply]`. Take element 0 as `exchange_lovelace`
and element 1 as `exchange_qada`, never from another UTxO, a cache or the
owner API. Neither the build nor the deployed scripts check the pair against
the market:

- A pair that raises the quantity (including a stale one, since the
  underlying per qADA only grows) mints more than the rate allows, and the
  qADA policy rejects the transaction.
- A pair that lowers the quantity validates, and the supplier receives less
  qADA for the same ADA. For the recorded snapshot, a swapped pair mints
  2 322 752 qADA instead of 4 305 236 194.

[`invoke-args/supply_ada.json`](invoke-args/supply_ada.json) replays the
recorded snapshot (100 ADA from a public preview wallet). Those UTxOs are
spent by the next batch; refresh the live inputs to build against the
current market.

## Expected effects

With `qada = floor(supply_lovelace × exchange_qada ÷ exchange_lovelace)`:

| Effect | Value |
|---|---|
| Action successor | At `ActionScript`, the spent action UTxO's value + `supply_lovelace`, the action token kept |
| Action datum | `[[supplied + supply_lovelace, minted + qada, f2, f3, f4], marker]`; `f2`–`f4` and `marker` unchanged |
| Mint | exactly `qada` of `qada_policy` (empty name) |
| Receipt | `qada` qADA plus change to `supplier` |
| Reference inputs | market state, market parameters and the action validator's reference script |
| Witness script | the qADA policy (PlutusV2), inline |
| Script data hash | the redeemers and the PlutusV2 language view only |
| Outputs | exactly the action successor and the receipt |
| Fee and change | separate from the deposit: the supplier's inputs minus `supply_lovelace` minus the fee |

Recorded snapshot: 100 000 000 lovelace at the pair
(4 524 623 833 756, 194 795 742 978 417) mints **4 305 236 194** qADA, the
same quantity as the owner's builder. Both transactions pass script
evaluation on preview, and both carry the PlutusV2-only script data hash. The
[comparison report](invoke-args/comparison-report.md) decodes them side by
side.

## Use it

```bash
trix use open-tx3/liqwid-v2:0.1.0
```

| Language | Package | Generate the client |
|---|---|---|
| TypeScript | [`tx3-sdk`](https://www.npmjs.com/package/tx3-sdk) (npm) | `trix codegen --plugin ts-client` |
| Rust | [`tx3-sdk`](https://crates.io/crates/tx3-sdk) (crates.io) | `trix codegen --plugin rust-client` |
| Python | [`tx3-sdk`](https://pypi.org/project/tx3-sdk/) (PyPI) | `trix codegen --plugin python-client` |
| Go | [`github.com/tx3-lang/go-sdk/sdk`](https://pkg.go.dev/github.com/tx3-lang/go-sdk/sdk) | `trix codegen --plugin go-client` |
| Java | [`land.tx3:tx3-sdk`](https://central.sonatype.com/artifact/land.tx3/tx3-sdk) (Maven Central) | `trix codegen --plugin java-client` |
| Swift | [`Tx3SDK`](https://github.com/tx3-lang/swift-sdk) (Swift Package Manager) | `trix codegen --plugin swift-client` |

To build the unsigned transaction from this directory:

```bash
trix invoke --profile preview --skip-submit --args-json-path invoke-args/supply_ada.json
```

The protocol guide, Fluent tools and Skill for this version are linked here
when they are published.

## Checks

`trix check` and `trix build`, then
[`scripts/check-supply.py`](../scripts/check-supply.py), which:

- fails when the built `supply_ada` TIR differs from the one the generated
  fixture was resolved from (the sha256 of its decoded TIR, map entries
  sorted, is recorded with the fixture), so a change to `main.tx3` needs a
  regenerated fixture;
- decodes the builder's and the generated supply against the snapshot,
  derives the qADA from the market state datum and asserts the effects above,
  including the exact outputs and the script data hash recomputed from the
  recorded cost model;
- requires each [negative fixture](fixtures/negative/) to be rejected for its
  named reason: wrong market, over-mint, under-mint, wrong receipt recipient,
  an extra output and the PlutusV3 language view.

The checks run on recorded transactions. They do not build against the live
market.

## License

Apache-2.0, see [LICENSE](../LICENSE). This interface describes Liqwid's
deployed contracts and copies none of their code.
