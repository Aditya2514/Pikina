"""
The learning layer's data collection (Phase 1).

Every action run through Pikina becomes one row in activity.db with a normalised,
privacy-safe key (see keys.py); an opt-in tracker records which app is focused
(app names only). Nothing here leaves the machine. See docs/activity-log.md.
"""
