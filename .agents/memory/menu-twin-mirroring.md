---
name: Menu twin-mirroring feature (bespoke, not a generic admin feature)
description: How two Telegram-bot menu subtrees are kept as live mirrors of each other (buttons, items, ratings, comments) — read before touching add_btn/upd_btn_label/del_btn/add_item/upd_item_content/del_item or ratings/comments functions in bot/data_access.py.
---

Two menu pairs (button ids 1966<->3101, 2017<->3106) are wired as bidirectional
"twins": any button/content added, edited, or deleted under one side is
automatically mirrored to the other, and ratings/comments are unified (stored
under one canonical id) rather than duplicated.

**Why:** user wanted two menus in different places to always show identical,
in-sync content, requested as a one-off scripted feature for these specific
IDs, not a generic reusable system.

**How to apply / mechanism:**
- `btn_twins` / `item_twins` Mongo collections hold bidirectional id pairs
  (`get_twin`/`set_twin`, `get_item_twin`/`set_item_twin` in `bot/data_access.py`).
- Core mutation functions (`add_btn`, `add_btn_before`, `add_btn_after`,
  `upd_btn_label`, `del_btn`, `add_item`, `upd_item_content`, `del_item`,
  `upd_items_desc`, `set_compound_text`, `set_btn_unified_rating`,
  `set_btn_hidden`, `toggle_btn_maintenance`, `set_btn_maintenance_msg`,
  `set_btn_no_caption`, `set_btn_no_btn_caption`, `toggle_sort_by_year`,
  `toggle_sort_alpha`) take an internal `_sync=True` param: when a twin
  exists they replay the same call on the twin with `_sync=False` to avoid
  infinite mutual recursion.
- Ratings/comments are NOT duplicated — `canonical_btn_id`/`canonical_item_id`
  (and `_canonical_target_id` for comments) normalize to `min(id, twin_id)`
  at read/write time in the rating/comment functions, so both sides
  transparently share one underlying record.
- Known gaps (accepted, not implemented): `clone_btn` internally uses raw
  Mongo inserts for nested quiz/exam/compound/menu children, bypassing the
  sync wrappers — cloning inside a twinned subtree won't mirror. Quiz/exam
  question-level CRUD (`add_exam_question` etc.) and button move/swap/reorder
  functions are also not twin-aware.
- One-off migration script that wiped 3101/3106 and deep-cloned 1966/2017
  into them (recording twin pairs node-by-node) lives at
  `scripts/migrate_link_menus.py` — reusable as a template if another pair
  ever needs the same bootstrap.
- **Link-on-clone UX (new):** after any clone operation the bot now asks
  the admin via inline keyboard "هل تريد ربط الزر المنسوخ بالأصل؟".
  Yes → `set_twin(source_bid, new_bid)` is called immediately.
  No → clone proceeds without linking.
  The existing hardcoded pairs (1966↔3101, 2017↔3106) remain linked as-is.
  Handler in `bot/message_handlers.py` (wait_clone_id state) stores pending
  data in `ctx.user_data["clone_link_pending"]`; resolved in
  `bot/callback_handlers.py` via `clone_link_yes_` / `clone_link_no_`.
