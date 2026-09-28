# UI tools

Run from the parent `ui` folder unless the selected module says otherwise.
Start with [installation](../README.md) or the [code guide](../CODE_GUIDE.md).
Read the module usage before running a helper with your private configuration.

| File | Responsibility and effects |
| --- | --- |
| [anam.py](anam.py) | Sets the configured identity orb or face; writes local/runtime state. |
| [canvas_vault_backfill.py](canvas_vault_backfill.py) | Optional canvas/archive maintenance; inspect configured paths before running. |
| [carry_check.py](carry_check.py) | Read-only size/day-block report for installer-supplied programs; none ship. |
| [demojibake.py](demojibake.py) | Repairs double-decoded export text; review input/output arguments before writing. |
| [qcall.py](qcall.py) | Calls the chosen Qualia operation, which may write remote data. |
| [story_agent_coverage.py](story_agent_coverage.py) | Read-only chapter coverage for an explicitly selected story folder. |
| [timer_check.py](timer_check.py) | Read-only armed-timer health report. |
| [tool_loop_guard.py](tool_loop_guard.py) | Optional repeated-call hook; stores temporary session state and needs explicit hook configuration. |
| [wrist_check.py](wrist_check.py) | Optional wearable check support. |
| [wrist_gate.py](wrist_gate.py) | Optional wearable integration gate. |
| [wrist_heard.py](wrist_heard.py) | Optional wearable acknowledgement support. |

Live diagnostics can make network requests and start provider processes.
Optional integrations need installer-owned credentials or content; see
[external inputs](../EXTERNAL_INPUTS.md). Keep filenames stable because runtime
configuration may refer to their exact paths.
