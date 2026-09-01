import threading
from unittest.mock import MagicMock, patch
from utils.thread_utils import attach_script_run_ctx


def test_attach_script_run_ctx_when_no_context():
    """When no Streamlit context is present, thread is returned without error."""
    t = threading.Thread(target=lambda: None)
    result = attach_script_run_ctx(t)
    assert result is t


def test_attach_script_run_ctx_when_context_present():
    """When a context is present, add_script_run_ctx is called."""
    t = threading.Thread(target=lambda: None)
    mock_ctx = MagicMock()
    with (
        patch("utils.thread_utils.get_script_run_ctx", return_value=mock_ctx),
        patch("utils.thread_utils.add_script_run_ctx") as mock_add,
    ):
        result = attach_script_run_ctx(t)
        assert result is t
        mock_add.assert_called_once_with(t, mock_ctx)
