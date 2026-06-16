# claude_test index

Debug / exploratory scripts for the PipetteLiquidHandler cell.

| File | Purpose | What was learned |
|------|---------|------------------|
| `dryrun_workflow.py` | Mock the three drivers (mks_motor / picus2 / entris_ii) and run the full `run()` workflow with arbitrary coordinates to verify ordering. | Confirmed the "raise, traverse, descend" model: each station is `move_X -> move_Z descend -> action -> retract`; target weights are read only after the retract (tip clear); blow-out on each pass's final dispense before its retract+weigh; sequential tip indexing (slots 0/1/2 spaced by interval); motor-on deferred to `setup()` over the trash bin. |
| `bringup_comms.py` | Real-hardware comms check via `connect(home=False)` — opens all three devices and issues info queries only (no motion). | All three verified live: balance `BCE224I-1SKR`, pipette `CP-7.0 / SINGLE_CHANNEL_1000UL`, three stage motors `[SETUP] OK` with IO=14 (sitting on IN_1 home limit). Confirmed pipette=ttyACM0 (24bc:2202) and balance=ttyACM1 (24bc:0010) auto-detect correctly by VID:PID. |
| `insert_ch1_docx.py` | Insert `chapter1_draft.md` into the thesis form docx, replacing the "Chapter 1. *****" template placeholder block. | Maps Markdown to the template's manual formatting (16 pt-bold title reusing the placeholder para so its preceding page break survives, 12 pt-bold subheadings, 12 pt body with 1.5 spacing + first-line indent); `**bold**` -> bold runs, code backticks stripped, top blockquote dropped, [TODO]/[ref] kept as text; page break before "Chapter 2" untouched. Backup at `*.docx.bak`. Verified: title + 4 subheadings + 14 body paras, inline bold (C.M./A.P./P.S. etc.) correct. |
