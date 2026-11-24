# Liqwid V2 `supply_ada` — comparison report

Compares the unsigned transaction this interface builds with the one the
owner's public supply builder returns for the same wallet, amount and market
state. `scripts/check-supply.py` repeats every row marked *checked* on each CI
run, from the fixtures in [`../fixtures`](../fixtures).

## Transactions

| Role | Tx hash | Source |
|---|---|---|
| Reference (owner builder) | `ab019024c7f21eb4a9789458eb76b1c6fe79215070acb8d7157339b86d78feb1` | `liqwid.transactions.supply` on `https://v2.api.preview.liqwid.dev/graphql`, market `Ada`, 100 000 000 lovelace ([fixture](../fixtures/reference-supply.json)) |
| Generated | `836b2fcbe3a1e1896a96cf4f824f6b2b66e2afc5a80cd07b9cf2bc9722fc3a6c` | `trix invoke --profile preview --skip-submit --args-json-path supply_ada.json`, trix 0.28.0 ([fixture](../fixtures/generated-supply.json)) |

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
| Generated | passes, same budget | passes, same budget |
| [`wrong-qada-quantity`](../fixtures/negative/wrong-qada-quantity.json) | — | **fails**: `'respectsRate' is false` |

The failing trace shows the rate the qADA policy applies,
`qTokenRate: 4524623833756/194795742978417`, the exchange pair in field 9 of
the market state datum.

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
| qADA recipient | `supplier` key `2ae10dbd…ad00`, with the change | same | checked |
| Required signer | `2ae10dbd…ad00` | same | checked |
| Reference inputs | action script, qADA script, market parameters, market state | same four | checked |
| Redeemers | `Constr 0 []` for both scripts | same | |

The builder rounds the same way: the exact quotient is 4 305 236 194.999, and
both mint the floor.

## Fees and change

Recorded apart from the deposit.

| | Reference | Generated |
|---|---|---|
| Fee | 600 143 | 1 685 565 |
| Change in the receipt output | 2 372 287 640 lovelace | 196 213 167 lovelace |
| Supplier input | `701cf5b5…f1ec#3` (2 472 887 783) | `b6f00291…3d2b#4` (297 898 732), picked by the resolver |
| Collateral | same input, with collateral return, total 5 000 000 | `b6f00291…3d2b#5` (5 000 000) |

## Negative fixtures

Each must be rejected for the named reason.

| Fixture | Change | Rejected as |
|---|---|---|
| [`wrong-market`](../fixtures/negative/wrong-market.json) | successor sent to the USDCx market's action validator with that market's token | `market` |
| [`wrong-qada-quantity`](../fixtures/negative/wrong-qada-quantity.json) | built with `exchange_qada` 1 % high: mints 4 348 288 556 | `qada-quantity` (and by the preview qADA policy) |
| [`wrong-receipt-recipient`](../fixtures/negative/wrong-receipt-recipient.json) | qADA output paid to another key | `receipt-recipient` |

## Expected differences

| Difference | Reason |
|---|---|
| Wallet inputs, change, collateral | The resolver picks the supplier's UTxOs itself. |
| Fee and redeemer budgets | `--skip-submit` builds with placeholder budgets (2 000 000 mem / 2 000 000 000 cpu) and a provisional fee; the evaluation above gives the real budgets. |
| TTL | The builder sets slot 128897418; this interface sets `tip_slot() + 300`. Neither sets a lower bound. |
| Exchange pair as input | Read from the market state by the caller: the current transaction resolver fails on that datum's big integers. The validator enforces the rate. |
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
[`market-snapshot.json`](../fixtures/market-snapshot.json) describes.
