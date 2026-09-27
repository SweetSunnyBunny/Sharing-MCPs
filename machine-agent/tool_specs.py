SERVERS = {
    "clipboard": {
        "display_name": "Clipboard Cloud Proxy",
        "default_port": 8788,
        "tools": [
            {"name": "clipboard_read", "doc": "Read the current clipboard text.", "params": []},
            {"name": "clipboard_write", "doc": "Write text into the clipboard.", "params": [
                {"name": "text", "type": "str"},
            ]},
            {"name": "clipboard_history", "doc": "Read recent clipboard history.", "params": [
                {"name": "limit", "type": "int", "default": "20"},
            ]},
            {"name": "clipboard_search", "doc": "Search clipboard history.", "params": [
                {"name": "query", "type": "str"},
            ]},
        ],
    },
    "desktop-control": {
        "display_name": "Desktop Control Cloud Proxy",
        "default_port": 8792,
        "tools": [
            {"name": "screenshot", "doc": "Take a screenshot.", "params": [
                {"name": "region", "type": "str | None", "default": "None"},
            ]},
            {"name": "get_screen_size", "doc": "Get screen resolution.", "params": []},
            {"name": "get_mouse_position", "doc": "Get the current mouse position.", "params": []},
            {"name": "mouse_move", "doc": "Move the mouse cursor.", "params": [
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
            ]},
            {"name": "mouse_click", "doc": "Click the mouse.", "params": [
                {"name": "x", "type": "int | None", "default": "None"},
                {"name": "y", "type": "int | None", "default": "None"},
                {"name": "button", "type": "str", "default": "\"left\""},
                {"name": "clicks", "type": "int", "default": "1"},
            ]},
            {"name": "mouse_scroll", "doc": "Scroll the mouse wheel.", "params": [
                {"name": "amount", "type": "int"},
                {"name": "x", "type": "int | None", "default": "None"},
                {"name": "y", "type": "int | None", "default": "None"},
            ]},
            {"name": "type_text", "doc": "Type text on the keyboard.", "params": [
                {"name": "text", "type": "str"},
                {"name": "interval", "type": "float", "default": "0.02"},
            ]},
            {"name": "hotkey", "doc": "Press a keyboard shortcut.", "params": [
                {"name": "keys", "type": "str"},
            ]},
            {"name": "key_press", "doc": "Press a single key.", "params": [
                {"name": "key", "type": "str"},
            ]},
            {"name": "list_windows", "doc": "List visible windows.", "params": []},
            {"name": "focus_window", "doc": "Focus a window by title.", "params": [
                {"name": "title", "type": "str"},
            ]},
            {"name": "locate_on_screen", "doc": "Locate an image on the screen.", "params": [
                {"name": "image_path", "type": "str"},
                {"name": "confidence", "type": "float", "default": "0.8"},
            ]},
        ],
    },
    "terminal": {
        "display_name": "Terminal Cloud Proxy",
        "default_port": 8793,
        "tools": [
            {"name": "terminal_execute", "doc": "Execute a command in a persistent session.", "params": [
                {"name": "command", "type": "str"},
                {"name": "session_id", "type": "str", "default": "\"\""},
                {"name": "timeout", "type": "int", "default": "120"},
            ]},
            {"name": "terminal_create", "doc": "Create a new terminal session.", "params": [
                {"name": "name", "type": "str", "default": "\"\""},
                {"name": "cwd", "type": "str", "default": "\"\""},
                {"name": "shell", "type": "str", "default": "\"\""},
            ]},
            {"name": "terminal_list", "doc": "List active terminal sessions.", "params": []},
            {"name": "terminal_destroy", "doc": "Destroy a terminal session.", "params": [
                {"name": "session_id", "type": "str"},
            ]},
            {"name": "terminal_get_info", "doc": "Get detailed terminal session info.", "params": [
                {"name": "session_id", "type": "str", "default": "\"\""},
            ]},
        ],
    },
    "filesystem": {
        "display_name": "Filesystem Cloud Proxy",
        "default_port": 8080,
        "tools": [
            {"name": "fs_list_directory", "doc": "List contents of a directory.", "params": [
                {"name": "path", "type": "str"},
                {"name": "show_hidden", "type": "bool", "default": "False"},
                {"name": "recursive", "type": "bool", "default": "False"},
                {"name": "pattern", "type": "str | None", "default": "None"},
            ]},
            {"name": "fs_create_directory", "doc": "Create a directory.", "params": [
                {"name": "path", "type": "str"},
                {"name": "parents", "type": "bool", "default": "True"},
            ]},
            {"name": "fs_read_file", "doc": "Read a text or binary file.", "params": [
                {"name": "path", "type": "str"},
                {"name": "encoding", "type": "str", "default": "\"utf-8\""},
                {"name": "start_line", "type": "int | None", "default": "None"},
                {"name": "num_lines", "type": "int | None", "default": "None"},
            ]},
            {"name": "fs_read_image", "doc": "Read an image as a bounded vision-capable preview.", "params": [
                {"name": "path", "type": "str"},
                {"name": "max_dimension", "type": "int", "default": "2048"},
                {"name": "quality", "type": "int", "default": "85"},
            ]},
            {"name": "fs_get_file_info", "doc": "Get file or directory metadata.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "fs_edit_file", "doc": "Atomically replace an exact text block; fails without writing if the match count differs.", "params": [
                {"name": "path", "type": "str"},
                {"name": "old_text", "type": "str"},
                {"name": "new_text", "type": "str"},
                {"name": "expected_replacements", "type": "int", "default": "1"},
                {"name": "encoding", "type": "str", "default": "\"utf-8\""},
            ]},
            {"name": "fs_write_file", "doc": "Write a text file.", "params": [
                {"name": "path", "type": "str"},
                {"name": "content", "type": "str"},
                {"name": "encoding", "type": "str", "default": "\"utf-8\""},
                {"name": "create_dirs", "type": "bool", "default": "True"},
                {"name": "append", "type": "bool", "default": "False"},
            ]},
            {"name": "fs_write_binary", "doc": "Write base64-encoded binary content.", "params": [
                {"name": "path", "type": "str"},
                {"name": "base64_content", "type": "str"},
                {"name": "create_dirs", "type": "bool", "default": "True"},
            ]},
            {"name": "fs_copy", "doc": "Copy a file or directory.", "params": [
                {"name": "source", "type": "str"},
                {"name": "destination", "type": "str"},
            ]},
            {"name": "fs_move", "doc": "Move a file or directory.", "params": [
                {"name": "source", "type": "str"},
                {"name": "destination", "type": "str"},
            ]},
            {"name": "fs_delete", "doc": "Delete a file or directory.", "params": [
                {"name": "path", "type": "str"},
                {"name": "recursive", "type": "bool", "default": "False"},
            ]},
            {"name": "fs_search", "doc": "Search for files by glob.", "params": [
                {"name": "path", "type": "str"},
                {"name": "pattern", "type": "str"},
                {"name": "max_results", "type": "int", "default": "100"},
            ]},
            {"name": "fs_search_content", "doc": "Search for text within files.", "params": [
                {"name": "path", "type": "str"},
                {"name": "text", "type": "str"},
                {"name": "file_pattern", "type": "str", "default": "\"*\""},
                {"name": "case_sensitive", "type": "bool", "default": "False"},
                {"name": "max_results", "type": "int", "default": "50"},
            ]},
            {"name": "terminal_execute", "doc": "Execute a command in a persistent local shell session on the connected machine.", "params": [
                {"name": "command", "type": "str"},
                {"name": "session_id", "type": "str", "default": "\"\""},
                {"name": "timeout", "type": "int", "default": "120"},
            ]},
            {"name": "fs_list_drives", "doc": "List drives or mount points.", "params": []},
            {"name": "fs_get_recent_files", "doc": "Find recently modified files.", "params": [
                {"name": "path", "type": "str"},
                {"name": "hours", "type": "int", "default": "24"},
                {"name": "pattern", "type": "str", "default": "\"*\""},
                {"name": "max_results", "type": "int", "default": "50"},
            ]},
        ],
    },
    "krita": {
        "display_name": "Krita Cloud Proxy",
        "default_port": 8787,
        "tools": [
            {"name": "krita_health", "doc": "Check Krita connectivity.", "params": []},
            {"name": "krita_new_canvas", "doc": "Create a new Krita canvas.", "params": [
                {"name": "width", "type": "int", "default": "800"},
                {"name": "height", "type": "int", "default": "600"},
                {"name": "name", "type": "str", "default": "\"New Canvas\""},
                {"name": "background", "type": "str", "default": "\"#1a1a2e\""},
            ]},
            {"name": "krita_set_color", "doc": "Set the current color.", "params": [
                {"name": "color", "type": "str"},
            ]},
            {"name": "krita_set_brush", "doc": "Set brush properties.", "params": [
                {"name": "preset", "type": "str | None", "default": "None"},
                {"name": "size", "type": "int | None", "default": "None"},
                {"name": "opacity", "type": "float | None", "default": "None"},
            ]},
            {"name": "krita_stroke", "doc": "Paint a stroke.", "params": [
                {"name": "points", "type": "list[list[int]]"},
                {"name": "pressure", "type": "float", "default": "1.0"},
            ]},
            {"name": "krita_fill", "doc": "Fill around a point.", "params": [
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
                {"name": "radius", "type": "int", "default": "50"},
            ]},
            {"name": "krita_draw_shape", "doc": "Draw a shape.", "params": [
                {"name": "shape", "type": "str"},
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
                {"name": "width", "type": "int", "default": "100"},
                {"name": "height", "type": "int", "default": "100"},
                {"name": "fill", "type": "bool", "default": "True"},
                {"name": "stroke", "type": "bool", "default": "False"},
                {"name": "x2", "type": "int | None", "default": "None"},
                {"name": "y2", "type": "int | None", "default": "None"},
            ]},
            {"name": "krita_export", "doc": "Export the current canvas.", "params": [
                {"name": "path", "type": "str"},
                {"name": "format", "type": "str", "default": "\"png\""},
            ]},
            {"name": "krita_save", "doc": "Save the current document.", "params": []},
            {"name": "krita_save_as", "doc": "Save the current document to a path.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "krita_request_save", "doc": "Request that the user save in Krita.", "params": [
                {"name": "suggested_path", "type": "str", "default": "\"\""},
            ]},
            {"name": "krita_undo", "doc": "Undo the last action.", "params": []},
            {"name": "krita_redo", "doc": "Redo the last action.", "params": []},
            {"name": "krita_clear", "doc": "Clear the current layer.", "params": [
                {"name": "color", "type": "str", "default": "\"#1a1a2e\""},
            ]},
            {"name": "krita_get_color_at", "doc": "Sample a color from the canvas.", "params": [
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
            ]},
            {"name": "krita_list_brushes", "doc": "List available brushes.", "params": [
                {"name": "filter", "type": "str", "default": "\"\""},
                {"name": "limit", "type": "int", "default": "20"},
            ]},
            {"name": "krita_gradient", "doc": "Draw a gradient.", "params": [
                {"name": "x1", "type": "int"},
                {"name": "y1", "type": "int"},
                {"name": "x2", "type": "int"},
                {"name": "y2", "type": "int"},
                {"name": "color1", "type": "str", "default": "\"#000000\""},
                {"name": "color2", "type": "str", "default": "\"#ffffff\""},
                {"name": "gradient_type", "type": "str", "default": "\"linear\""},
            ]},
            {"name": "krita_text", "doc": "Add text to the canvas.", "params": [
                {"name": "text", "type": "str"},
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
                {"name": "font_size", "type": "int", "default": "24"},
                {"name": "color", "type": "str", "default": "\"#ffffff\""},
                {"name": "font", "type": "str", "default": "\"Arial\""},
            ]},
            {"name": "krita_flood_fill", "doc": "Flood fill a region.", "params": [
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
                {"name": "tolerance", "type": "int", "default": "20"},
            ]},
            {"name": "krita_new_layer", "doc": "Create a new layer.", "params": [
                {"name": "name", "type": "str", "default": "\"New Layer\""},
            ]},
            {"name": "krita_delete_layer", "doc": "Delete the current layer.", "params": []},
            {"name": "krita_list_layers", "doc": "List layers.", "params": []},
            {"name": "krita_set_layer_opacity", "doc": "Set layer opacity.", "params": [
                {"name": "opacity", "type": "int"},
            ]},
            {"name": "krita_select_layer", "doc": "Select a layer by name.", "params": [
                {"name": "name", "type": "str"},
            ]},
            {"name": "krita_duplicate_layer", "doc": "Duplicate the current layer.", "params": []},
            {"name": "krita_merge_down", "doc": "Merge the current layer down.", "params": []},
            {"name": "krita_transform", "doc": "Transform the current selection or layer.", "params": [
                {"name": "operation", "type": "str"},
            ]},
            {"name": "krita_select_rectangle", "doc": "Create a rectangular selection.", "params": [
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
                {"name": "width", "type": "int"},
                {"name": "height", "type": "int"},
            ]},
            {"name": "krita_select_ellipse", "doc": "Create an elliptical selection.", "params": [
                {"name": "x", "type": "int"},
                {"name": "y", "type": "int"},
                {"name": "width", "type": "int"},
                {"name": "height", "type": "int"},
            ]},
            {"name": "krita_select_all", "doc": "Select all.", "params": []},
            {"name": "krita_deselect", "doc": "Clear the selection.", "params": []},
            {"name": "krita_invert_selection", "doc": "Invert the current selection.", "params": []},
            {"name": "krita_filter", "doc": "Apply a filter.", "params": [
                {"name": "name", "type": "str"},
                {"name": "strength", "type": "int", "default": "5"},
            ]},
            {"name": "krita_get_document_info", "doc": "Get current document metadata.", "params": []},
            {"name": "krita_resize_canvas", "doc": "Resize the canvas.", "params": [
                {"name": "width", "type": "int | None", "default": "None"},
                {"name": "height", "type": "int | None", "default": "None"},
                {"name": "anchor", "type": "str", "default": "\"center\""},
            ]},
            {"name": "krita_crop_to_selection", "doc": "Crop to the current selection.", "params": []},
            {"name": "krita_bezier_curve", "doc": "Draw a bezier curve.", "params": [
                {"name": "points", "type": "list[list[int]]"},
                {"name": "size", "type": "int", "default": "3"},
            ]},
        ],
    },
    "muse-tts": {
        "display_name": "MUSE TTS Cloud Proxy",
        "default_port": 8794,
        "tools": [
            {"name": "muse_speak", "doc": "Speak text out loud on the machine agent host.", "params": [
                {"name": "text", "type": "str"},
                {"name": "voice", "type": "str", "default": "\"\""},
                {"name": "clone", "type": "str", "default": "\"\""},
                {"name": "ref_audio", "type": "str", "default": "\"\""},
                {"name": "speed", "type": "float", "default": "0"},
            ]},
            {"name": "muse_list_voices", "doc": "List available voices and clones.", "params": [
                {"name": "language", "type": "str", "default": "\"\""},
            ]},
            {"name": "muse_check", "doc": "Check TTS engine status.", "params": []},
        ],
    },
    "books-tools": {
        "display_name": "Books Tools Cloud Proxy",
        "default_port": 8795,
        "tools": [
            {"name": "list_books", "doc": "List available EPUB books.", "params": []},
            {"name": "get_book_info", "doc": "Get metadata and reading progress for a book.", "params": [
                {"name": "book", "type": "str"},
            ]},
            {"name": "read_chapter", "doc": "Read a chapter from a book.", "params": [
                {"name": "book", "type": "str"},
                {"name": "chapter", "type": "int | None", "default": "None"},
                {"name": "continue_reading", "type": "bool", "default": "True"},
            ]},
            {"name": "search_book", "doc": "Search within a book.", "params": [
                {"name": "book", "type": "str"},
                {"name": "query", "type": "str"},
            ]},
            {"name": "add_bookmark", "doc": "Bookmark a chapter.", "params": [
                {"name": "book", "type": "str"},
                {"name": "chapter", "type": "int"},
                {"name": "note", "type": "str | None", "default": "None"},
            ]},
            {"name": "add_reading_note", "doc": "Save a reading note for a book.", "params": [
                {"name": "book", "type": "str"},
                {"name": "chapter", "type": "int"},
                {"name": "note", "type": "str"},
                {"name": "quote", "type": "str | None", "default": "None"},
            ]},
            {"name": "get_reading_notes", "doc": "Get saved reading notes.", "params": [
                {"name": "book", "type": "str | None", "default": "None"},
            ]},
            {"name": "summarize_chapter", "doc": "Prepare a chapter for summarization.", "params": [
                {"name": "book", "type": "str"},
                {"name": "chapter", "type": "int"},
            ]},
        ],
    },
    "obsidian": {
        "display_name": "Obsidian Cloud Proxy",
        "default_port": 8796,
        "tools": [
            {"name": "read_note", "doc": "Read a note from the Obsidian vault.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "write_note", "doc": "Create or overwrite a note.", "params": [
                {"name": "path", "type": "str"},
                {"name": "content", "type": "str"},
                {"name": "createFrontmatter", "type": "bool | None", "default": "None"},
                {"name": "frontmatter", "type": "dict | None", "default": "None"},
                {"name": "overwrite", "type": "bool | None", "default": "None"},
            ]},
            {"name": "append_to_note", "doc": "Append content to an existing note.", "params": [
                {"name": "path", "type": "str"},
                {"name": "content", "type": "str"},
                {"name": "addTimestamp", "type": "bool | None", "default": "None"},
                {"name": "separator", "type": "str | None", "default": "None"},
            ]},
            {"name": "delete_note", "doc": "Delete a note from the vault.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "move_note", "doc": "Move or rename a note.", "params": [
                {"name": "sourcePath", "type": "str"},
                {"name": "destPath", "type": "str"},
            ]},
            {"name": "list_notes", "doc": "List notes in a vault directory.", "params": [
                {"name": "path", "type": "str | None", "default": "None"},
                {"name": "recursive", "type": "bool | None", "default": "None"},
                {"name": "includeContent", "type": "bool | None", "default": "None"},
            ]},
            {"name": "list_folders", "doc": "List folders in the vault.", "params": [
                {"name": "path", "type": "str | None", "default": "None"},
            ]},
            {"name": "create_folder", "doc": "Create a vault folder.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "search_notes", "doc": "Full-text search across notes.", "params": [
                {"name": "query", "type": "str"},
                {"name": "caseSensitive", "type": "bool | None", "default": "None"},
                {"name": "includeContent", "type": "bool | None", "default": "None"},
                {"name": "maxResults", "type": "int | None", "default": "None"},
                {"name": "searchPath", "type": "str | None", "default": "None"},
            ]},
            {"name": "search_by_tag", "doc": "Find notes by tag.", "params": [
                {"name": "tag", "type": "str"},
                {"name": "includeContent", "type": "bool | None", "default": "None"},
                {"name": "maxResults", "type": "int | None", "default": "None"},
            ]},
            {"name": "get_recent_notes", "doc": "Get recently modified notes.", "params": [
                {"name": "limit", "type": "int | None", "default": "None"},
                {"name": "days", "type": "int | None", "default": "None"},
            ]},
            {"name": "list_tags", "doc": "List all tags in the vault.", "params": []},
            {"name": "get_backlinks", "doc": "Find backlinks to a note.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "get_outgoing_links", "doc": "List outgoing links from a note.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "get_frontmatter", "doc": "Read note frontmatter.", "params": [
                {"name": "path", "type": "str"},
            ]},
            {"name": "update_frontmatter", "doc": "Merge updates into note frontmatter.", "params": [
                {"name": "path", "type": "str"},
                {"name": "updates", "type": "dict"},
            ]},
            {"name": "list_templates", "doc": "List available templates.", "params": []},
            {"name": "create_from_template", "doc": "Create a note from a template.", "params": [
                {"name": "template", "type": "str"},
                {"name": "destPath", "type": "str"},
                {"name": "variables", "type": "dict | None", "default": "None"},
            ]},
            {"name": "create_daily_note", "doc": "Create or fetch a daily note.", "params": [
                {"name": "date", "type": "str | None", "default": "None"},
                {"name": "template", "type": "str | None", "default": "None"},
                {"name": "folder", "type": "str | None", "default": "None"},
            ]},
            {"name": "add_journal_entry", "doc": "Append a journal entry.", "params": [
                {"name": "content", "type": "str"},
                {"name": "journalPath", "type": "str | None", "default": "None"},
                {"name": "author", "type": "str | None", "default": "None"},
            ]},
            {"name": "get_vault_stats", "doc": "Get vault statistics.", "params": []},
            {"name": "get_vault_path", "doc": "Get the current vault path.", "params": []},
        ],
    },
}
# Anam live tool doorway (stable schema; live inventory comes from Anam).
SERVERS['filesystem']['tools'].extend([{'name': 'anam_discover', 'doc': 'Discover live Anam tools and exact input schemas: Hearth, Commons, Qualia Studio, saved reply canvases, history, computer control, and browser profiles. Search words or select a server; do this before anam_invoke.', 'annotations': {'readOnlyHint': True, 'openWorldHint': True}, 'params': [{'name': 'query', 'type': 'str', 'default': '""'}, {'name': 'server', 'type': 'str', 'default': '""'}, {'name': 'offset', 'type': 'int', 'default': '0'}, {'name': 'limit', 'type': 'int', 'default': '15'}, {'name': 'include_schema', 'type': 'bool', 'default': 'True'}]}, {'name': 'anam_invoke', 'doc': 'Execute a discovered Anam tool once. May read, write, communicate, or control the computer. Use exact server/tool and arguments_json matching its schema, plus your active identity. Give each action a unique request_id; reuse it only on transport retry. Collect running jobs with anam_job; do not resubmit.', 'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': True}, 'params': [{'name': 'server', 'type': 'str'}, {'name': 'tool', 'type': 'str'}, {'name': 'arguments_json', 'type': 'str'}, {'name': 'identity', 'type': 'str'}, {'name': 'conversation_id', 'type': 'str', 'default': '""'}, {'name': 'request_id', 'type': 'str', 'default': '""'}]}, {'name': 'anam_job', 'doc': 'Collect a running Anam tool result without repeating its action. Preserves native images/audio. Completed results survive restarts for up to seven days within a bounded store.', 'annotations': {'readOnlyHint': True, 'openWorldHint': False}, 'params': [{'name': 'job_id', 'type': 'str'}]}, {'name': 'anam_result', 'doc': 'Read or search the full stored text of a completed Anam tool result without repeating its action. Use next_offset for another page, or query to find relevant text.', 'annotations': {'readOnlyHint': True, 'openWorldHint': False}, 'params': [{'name': 'job_id', 'type': 'str'}, {'name': 'offset', 'type': 'int', 'default': '0'}, {'name': 'limit', 'type': 'int', 'default': '8000'}, {'name': 'query', 'type': 'str', 'default': '""'}]}])
