from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_identity_cache_paints_before_blocking_stylesheets():
    html = _source("static/index.html")

    paint = html.index("anam-identity-paint")
    main_css = html.index("/static/css/main.css")
    assert paint < main_css


def test_page_unload_does_not_delete_durable_attachment_chips():
    chat = _source("static/js/chat.js")
    unload = chat.split("window.addEventListener('beforeunload'", 1)[1]
    unload = unload.split("});", 1)[0]

    assert "clearImagePreview" not in unload
    assert "_cancelStreamRenderer" in unload


def test_attachment_state_is_scoped_and_parked_during_navigation():
    chat = _source("static/js/chat.js")
    app = _source("static/js/app.js")
    sidebar = _source("static/js/sidebar.js")

    assert "_pendingAttachmentChipsKey(identity" in chat
    assert "ownerIdentity: App.currentIdentity" in chat
    assert "parkAttachmentPreview()" in chat
    assert "Chat.parkAttachmentPreview();" in app
    assert "Chat.parkAttachmentPreview();" in sidebar


def test_native_picker_omits_video_extensions_that_break_android():
    """Video extensions in `accept` must stay OUT of the native file input."""
    html = _source("static/index.html")

    file_input = html.split('id="file-input"', 1)[1].split(">", 1)[0]
    for extension in (".mp4", ".mov", ".mkv", ".avi"):
        assert extension not in file_input


def test_video_files_are_still_classified_and_accepted():
    """Narrowing the picker must not narrow what Anam can actually receive."""
    chat = _source("static/js/chat.js")
    config = _source("config.py")

    assert "isVideoMime" in chat
    for extension in ("mp4", "mov", "mkv", "avi"):
        assert f"'{extension}'" in chat
        assert f'".{extension}"' in config


def test_one_picker_prefers_file_system_api_even_on_touch_devices():
    """Do not reintroduce the Android-only bypass that caused the action sheet.

    The File System Access picker was the working one-picker flow on Owner's
    phone. Sending touch devices straight to the broad fallback input replaced
    it with Android's Camera/Camcorder/Files chooser and selections stopped
    reaching the page.
    """
    chat = _source("static/js/chat.js")
    picker = chat.split("async openUniversalFilePicker(", 1)[1]
    picker = picker.split("\n    onFileSelected(file)", 1)[0]

    assert "showOpenFilePicker" in picker
    assert "fallbackInput.click()" in picker
    assert picker.index("showOpenFilePicker") < picker.rindex("fallbackInput.click()")
    assert "navigator.maxTouchPoints" not in picker
    assert "'ontouchstart' in window" not in picker


def test_attach_button_uses_exactly_one_picker_path():
    html = _source("static/index.html")
    chat = _source("static/js/chat.js")

    assert html.count('id="file-input"') == 1
    assert 'id="image-input"' not in html
    attach_setup = chat.split("const attachBtn = document.getElementById('attach-btn');", 1)[1]
    attach_setup = attach_setup.split("previewRemove.addEventListener", 1)[0]
    assert "this.openUniversalFilePicker(fileInput);" in attach_setup
    assert "imageInput.click()" not in attach_setup
