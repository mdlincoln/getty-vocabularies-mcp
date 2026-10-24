# Test fixtures

Captured on 2026-10-23 from `https://vocab.getty.edu/{vocab}/{id}.json` with
`curl`, then trimmed: array members the parser never reads were dropped
(the `referred_to_by` source lists inside names and scope notes are most of
the bulk) and long arrays were cut. Payload order and every key the parser
reads are unchanged, so each file is a faithful sample of the served Linked
Art shape at that date.

| File | Source record | Notes |
|---|---|---|
| `aat-300177433.json` | AAT `300177433` | frescoes; has a scope note, `la:related_from_by`, no `narrower` |
| `aat-300033701.json` | AAT `300033701` | guide term `<paintings by material or technique>`; 18 `narrower`, **no** `subject_of` |
| `tgn-7011179.json` | TGN `7011179` | Siena; parents in `part_of`; preferred name is the 6th entry; geometry node in `identified_by` |
| `tgn-7004334.json` | TGN `7004334` | Nürnberg; preferred name is the first entry |
| `ulan-500115493.json` | ULAN `500115493` | Dürer; `born`/`died`, roles, nationalities, biography, `la:related_from_by` |

Of the five, only `aat-300033701` carries no scope note, which is why the
absence path needs no derived fixture.

## Derived fixtures

These are hand-built derivatives, not captured payloads. Each exists for a
path the captured records cannot reach:

| File | Derivation |
|---|---|
| `aat-300177433-no-preflabel.json` | `aat-300177433.json` with `_label` and `identified_by` removed, so no preferred label resolves |
| `aat-300177433-bare-classification.json` | `aat-300177433.json` with every `classified_as` list rewritten to bare URI strings instead of `{"id": …}` objects |
| `cycle-a.json`, `cycle-b.json` | minimal hand-written pair listing each other as a broader term; `cycle-a` also lists *itself* as a narrower term, so the hierarchy walk's visited set is exercised |

`tests/test_live.py` is the guard against the captured shape drifting away
from what the endpoint serves today: it is skipped unless `GETTY_LIVE=1`.
