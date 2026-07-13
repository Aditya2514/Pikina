# Pikina OS — Phase 4e Observation Log

This log records 16 real-world queries evaluated against the local Ollama (Llama3) model.

| # | Input Query | Target Tool | Proposed JSON Action | Outcome / Status | Latency |
|---|---|---|---|---|---|
| 1 | `open notepad` | `—` | `—` | error (timeout) | 24.91s |
| 2 | `start the calculator` | `utility.calculate` | `{"tool": "utility.calculate", "params": {}, "claimed_permission_level": 0, "provenance": "model_output"}` | error (validation_failed): bad_params | 18.94s |
| 3 | `look up files with resume in the name` | `fs.find_file` | `{"tool": "fs.find_file", "params": {"root_directory": "", "name_pattern": "resume"}, "claimed_permission_level": 0, "provenance": "model_output"}` | error (validation_failed): bad_params | 20.85s |
| 4 | `add event college project meeting on July 20th at 3pm` | `calendar.add_event` | `{"tool": "calendar.add_event", "params": {"type": "meeting", "time": "2023-07-20T15:00:00", "recurrence": null}, "claimed_permission_level": 0, "provenance": "model_output"}` | error (validation_failed): bad_params | 24.33s |
| 5 | `add a task to call mom by tonight` | `todo.add` | `{"tool": "todo.add", "params": {"task": "Call mom by tonight"}, "claimed_permission_level": 1, "provenance": "model_output"}` | error (validation_failed): bad_params | 19.38s |
| 6 | `mark task call mom as done` | `todo.complete` | `{"tool": "todo.complete", "params": {"task_text": "call mom"}, "claimed_permission_level": 2, "provenance": "model_output"}` | error (validation_failed): bad_params | 19.42s |
| 7 | `recall what i did yesterday` | `memory.recall` | `{"tool": "memory.recall", "params": {"query": "what i did yesterday"}, "claimed_permission_level": 1, "provenance": "model_output"}` | error (validation_failed): model_claimed_level_1_actual_0 | 19.62s |
| 8 | `list all pending tasks` | `todo.list` | `{"tool": "todo.list", "params": {"status": "pending"}, "claimed_permission_level": 2, "provenance": "model_output"}` | error (validation_failed): model_claimed_level_2_actual_0 | 18.22s |
| 9 | `quiet hours from 22:00 to 08:00` | `fs.find_file` | `{"tool": "fs.find_file", "params": {}, "claimed_permission_level": 0, "provenance": "model_output"}` | error (validation_failed): bad_params | 22.71s |
| 10 | `be formal` | `memory.recall` | `{"tool": "memory.recall", "params": {}, "claimed_permission_level": 0, "provenance": "model_output"}` | error (validation_failed): bad_params | 7.67s |
| 11 | `calculate 45 * 2 + 10` | `utility.calculate` | `{"tool": "utility.calculate", "params": {"expression": "45 * 2 + 10"}, "claimed_permission_level": 0, "provenance": "model_output"}` | error (validation_failed): bad_params | 19.22s |
| 12 | `start the stopwatch` | `—` | `—` | ok | 17.95s |
| 13 | `reset the stopwatch` | `—` | `—` | ok | 9.50s |
| 14 | `can you launch something to write some text` | `app.open` | `{"tool": "app.open", "params": {"executable_name": "notepad.exe"}, "claimed_permission_level": 1, "provenance": "model_output"}` | error (validation_failed): bad_params | 19.78s |
| 15 | `where is my presentation pdf` | `—` | `—` | success | 19.66s |
| 16 | `tell me a joke` | `—` | `—` | success | 19.43s |