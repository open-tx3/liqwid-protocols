# Liqwid V2 `supply_ada` — comparison report

Compares the unsigned transaction this interface builds with the one the
owner's public supply builder returns for the same wallet, amount and market
state. On each CI run, `scripts/check-supply.py` first checks that the built
`supply_ada` TIR is the one the generated transaction was resolved from, then
repeats every row marked *checked* from the fixtures in
[`../fixtures`](../fixtures). It does not rebuild against the live market.

## Transactions

| Role | Tx hash | Source |
|---|---|---|
| Reference (owner builder) | `ab019024c7f21eb4a9789458eb76b1c6fe79215070acb8d7157339b86d78feb1` | `liqwid.transactions.supply` on `https://v2.api.preview.liqwid.dev/graphql`, market `Ada`, 100 000 000 lovelace ([fixture](../fixtures/reference-supply.json)) |
| Generated | `9b417dc951b511a42b0d59b703ec43a1795964a3baac0711115ace4544017128` | `trix invoke --profile preview --skip-submit --args-json-path supply_ada.json`, trix 0.28.0 ([fixture](../fixtures/generated-supply.json)) |

Both are unsigned and were never submitted. Both were built on 2026-11-24
against the market as recorded in
[`market-snapshot.json`](../fixtures/market-snapshot.json): action UTxO
`67c41413…b284#3` and market state `67c41413…b284#1`, both created by the
batch at preview block 4889425.

## Validation on preview

Each transaction was evaluated against the preview ledger (Ogmios
`evaluateTransaction`, which runs the scripts without signatures or
submission):

| Transaction | Action spend | qADA mint |
|---|---|---|
| Reference | passes, 895 326 mem / 313 218 392 cpu | passes, 1 740 437 mem / 544 638 750 cpu |
| Generated | passes, 889 304 mem / 311 383 735 cpu | passes, 1 734 415 mem / 542 804 093 cpu |
| [`wrong-qada-quantity`](../fixtures/negative/wrong-qada-quantity.json) (over-mint) | — | **fails**: `'respectsRate' is false` |
| [`under-mint`](../fixtures/negative/under-mint.json) | passes | **passes** |
| [`wrong-market`](../fixtures/negative/wrong-market.json) | fails | fails |
| [`wrong-receipt-recipient`](../fixtures/negative/wrong-receipt-recipient.json), [`extra-output`](../fixtures/negative/extra-output.json) | passes | passes |

The generated budgets are slightly lower because its script context has one
reference input fewer (the qADA policy is inline). The failing trace shows the
rate the qADA policy applies, `qTokenRate: 4524623833756/194795742978417`, the
exchange pair in field 9 of the market state datum. The policy rejects only a
quantity above that rate: the under-mint validates.

## Script data hash

Script evaluation does not check the script data hash (body key 11); the
ledger does, in phase 1, before any script runs. Both scripts are PlutusV2, so
the hash must cover the redeemers and the PlutusV2 language view only, with
the cost model of the submission epoch (recorded for epoch 1491 in the
snapshot).

| Transaction | Key 11 | Language view |
|---|---|---|
| Reference | `8a8f9a37…b0f2` | PlutusV2 (checked) |
| Generated | `8699021f…4cbf` | PlutusV2 (checked) |
| [`wrong-language-view`](../fixtures/negative/wrong-language-view.json) | `a2dba8ae…8a8b` | PlutusV3, as the previous revision built it with the qADA policy as a reference script |

The resolver does not report a reference script's language, and the compiler
then falls back to the PlutusV3 view. Attaching the qADA policy inline gives
the compiler a PlutusV2 witness script to read the language from.

## Effects

| Effect | Reference | Generated | |
|---|---|---|---|
| Spent action UTxO | `67c41413…b284#3` | same | checked |
| Successor address | action validator `da3d53de…3747`, stake key `ab191e6a…805d` | same | checked |
| Successor value | 653 422 374 153 lovelace + action token `af829f44…8786` | same | checked |
| ADA supplied (successor − spent) | 100 000 000 | 100 000 000 | checked |
| Exchange pair (market state field 9) | 4 524 623 833 756 / 194 795 742 978 417 | same | checked |
| qADA due = ⌊100 000 000 × 194 795 742 978 417 ÷ 4 524 623 833 756⌋ | 4 305 236 194 | 4 305 236 194 | checked |
| Mint | 4 305 236 194 `50e015ec…f370` (empty name), nothing else | same | checked |
| Action datum before | `[[0, 0, 0, 0, 0], 610400975]` | same | checked |
| Action datum after | `[[100000000, 4305236194, 0, 0, 0], 610400975]` | same, identical bytes | checked |
| qADA recipient | `supplier` address (key `2ae10dbd…ad00`, stake `d997e3f9…97e2`), with the change | same | checked |
| Outputs | the successor and the receipt only | same | checked |
| Required signer | `2ae10dbd…ad00` | same | checked |
| Reference inputs | action script, qADA script, market parameters, market state | action script, market parameters, market state | checked |
| qADA policy | from its reference script | inline PlutusV2 witness | checked |
| Redeemers | `Constr 0 []` for both scripts | same | |

The builder rounds the same way: the exact quotient is 4 305 236 194.999, and
both mint the floor.

## Fees and change

Recorded apart from the deposit.

| | Reference | Generated |
|---|---|---|
| Fee | 600 143 | 1 903 937 (provisional, see below) |
| Change in the receipt output | 2 372 287 640 lovelace | 195 994 795 lovelace |
| Supplier input | `701cf5b5…f1ec#3` (2 472 887 783) | `b6f00291…3d2b#4` (297 898 732), picked by the resolver |
| Collateral | same input, with collateral return, total 5 000 000 | `b6f00291…3d2b#5` (5 000 000) |

The inline qADA policy adds 4 991 bytes to the transaction, which costs more
in size fee than the reference-script fee it saves.

## Negative fixtures

Each must be rejected for the named reason.

| Fixture | Change | Rejected as |
|---|---|---|
| [`wrong-market`](../fixtures/negative/wrong-market.json) | successor sent to the USDCx market's action validator with that market's token | `market` |
| [`wrong-qada-quantity`](../fixtures/negative/wrong-qada-quantity.json) | built with `exchange_qada` 1 % high: mints 4 348 288 556 | `qada-quantity` (and by the preview qADA policy) |
| [`under-mint`](../fixtures/negative/under-mint.json) | built with the exchange pair swapped: mints 2 322 752 | `qada-quantity` (the preview scripts accept it) |
| [`wrong-receipt-recipient`](../fixtures/negative/wrong-receipt-recipient.json) | qADA output paid to another key | `receipt-recipient` |
| [`extra-output`](../fixtures/negative/extra-output.json) | a third output pays 50 ADA of the change to another key | `outputs` |
| [`wrong-language-view`](../fixtures/negative/wrong-language-view.json) | script data hash over the PlutusV3 language view | `script-data-hash` |
| [`extraneous-script`](../fixtures/negative/extraneous-script.json) | the qADA reference-script UTxO added as a reference input beside the inline witness (the ledger rejects it as an extraneous script witness; preview evaluation passes) | `scripts` |

## Expected differences

| Difference | Reason |
|---|---|
| Wallet inputs, change, collateral | The resolver picks the supplier's UTxOs itself. |
| Fee and redeemer budgets | `--skip-submit` builds with placeholder budgets (2 000 000 mem / 2 000 000 000 cpu) and a provisional fee; the evaluation above gives the real budgets. |
| qADA policy inline | Needed for the PlutusV2 language view; see above. |
| TTL | The builder sets slot 128897418; this interface sets `tip_slot() + 300`. Neither sets a lower bound. |
| Exchange pair as input | Read from the market state by the caller: the current transaction resolver fails on that datum's big integers. Nothing checks the pair: the qADA policy rejects only a pair that over-mints. |
| Witnesses | Both transactions are unsigned. |

## Reproduce

```bash
cd liqwid-v2
trix build
python3 ../scripts/check-supply.py
```

`trix invoke --profile preview --skip-submit --args-json-path invoke-args/supply_ada.json`
works only until the next batch spends the recorded action and market state
UTxOs. Afterwards, refresh `action_utxo`, `market_state_utxo`,
`exchange_lovelace` and `exchange_qada` from the chain, as
[`market-snapshot.json`](../fixtures/market-snapshot.json) describes, taking
the pair from the datum of that same market state UTxO.
