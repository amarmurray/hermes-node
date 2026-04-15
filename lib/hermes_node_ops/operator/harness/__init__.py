"""File-backed operator harness state.

The harness is additive to the existing operator session model:
- plan.md
- todo.md
- scratch/{step_id}.md
- subagents/{subagent_id}/...

No new database tables. All state stays under the parent session directory.
"""

from .store import (
    complete_todo_item,
    ensure_harness_layout,
    list_subagents,
    read_plan,
    read_scratch,
    read_subagent_result,
    read_todo_items,
    read_todo_markdown,
    spawn_subagent,
    update_plan,
    update_todo_item,
    write_plan,
    write_scratch,
    write_todo_items,
)

__all__ = [
    "complete_todo_item",
    "ensure_harness_layout",
    "list_subagents",
    "read_plan",
    "read_scratch",
    "read_subagent_result",
    "read_todo_items",
    "read_todo_markdown",
    "spawn_subagent",
    "update_plan",
    "update_todo_item",
    "write_plan",
    "write_scratch",
    "write_todo_items",
]
