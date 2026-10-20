"""Live end-to-end test for the real CLI path.

This test spawns the real engine binary and runs the CLI with default config,
sending 2 turns and verifying successful responses.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest


def get_python_executable() -> str:
    """Get the Python executable from the virtual environment."""
    # Use the same Python that's running the tests
    return sys.executable


def is_engine_running() -> bool:
    """Check if tom-engine process is running."""
    import subprocess
    try:
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq tom-engine.exe"], 
                                capture_output=True, text=True, timeout=5)
        return "tom-engine.exe" in result.stdout
    except Exception:
        return False


@pytest.fixture
def isolated_log_env():
    """Create isolated log environment for subprocess."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", prefix="tom_e2e_", delete=False
    ) as f:
        log_file = f.name

    session_id = uuid.uuid4().hex[:8]
    
    env = os.environ.copy()
    env["TOM_LOG_FILE"] = log_file
    env["TOM_SESSION_ID"] = session_id
    # Ensure python package is on path
    project_root = Path(__file__).parent.parent.parent
    env["PYTHONPATH"] = str(project_root / "python") + os.pathsep + env.get("PYTHONPATH", "")
    
    yield {"log_file": log_file, "session_id": session_id, "env": env}
    
    # Clean up
    try:
        os.unlink(log_file)
    except OSError:
        pass


@pytest.mark.live
@pytest.mark.skipif(not is_engine_running(), reason="tom-engine not running")
class TestCLIRealPath:
    """Test the real CLI path with engine and llama-server."""

    def test_cli_two_turns(self, isolated_log_env):
        """Run CLI with 2 turns, verify non-empty replies and no errors."""
        # Use subprocess to run the CLI with piped input
        input_text = "Remember the word KANGAROO.\nWhat was the word I asked you to remember?\n"
        
        # Run the CLI command
        python_exe = get_python_executable()
        result = subprocess.run(
            [python_exe, "-m", "tom.main", "--cli"],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=Path(__file__).parent.parent.parent,
            env=isolated_log_env["env"],
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Check stdout has no JSON log lines (non-verbose mode)
        lines = stdout.strip().split('\n')
        json_lines = [l for l in lines if l.startswith('{') and '"timestamp"' in l]
        assert len(json_lines) == 0, f"Found JSON log lines in stdout: {json_lines}"
        
        # Check no "Model error" in output
        assert "Model error" not in stdout, f"Found 'Model error' in stdout: {stdout}"
        
        # Check no "Insufficient" in output
        assert "Insufficient" not in stdout, f"Found 'Insufficient' in stdout: {stdout}"
        
        # Check both replies are non-empty (should contain responses)
        # The output format: banner, prompt1, response1, prompt2, response2
        assert "KANGAROO" in stdout.upper(), f"Expected 'KANGAROO' in response, got: {stdout}"
        
        # Verify exit code is 0
        assert result.returncode == 0, f"CLI exited with code {result.returncode}, stderr: {stderr}"
        
        # Verify logs went to temp file with correct session_id
        with open(isolated_log_env["log_file"], "r") as f:
            log_lines = f.readlines()
        assert len(log_lines) > 0, "Expected log entries in temp log file"
        for line in log_lines:
            assert f'"session_id": "{isolated_log_env["session_id"]}"' in line, f"Log missing session_id: {line}"

    def test_cli_two_turns_verbose(self, isolated_log_env):
        """Run CLI with 2 turns in verbose mode, verify no JSON logs in stdout."""
        input_text = "Remember the word KANGAROO.\nWhat was the word I asked you to remember?\n"
        
        python_exe = get_python_executable()
        result = subprocess.run(
            [python_exe, "-m", "tom.main", "--cli", "--verbose"],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=Path(__file__).parent.parent.parent,
            env=isolated_log_env["env"],
        )
        
        stdout = result.stdout
        stderr = result.stderr
        
        # Check stdout has no JSON log lines (verbose mode - logs go to stderr)
        lines = stdout.strip().split('\n')
        json_lines = [l for l in lines if l.startswith('{') and '"timestamp"' in l]
        assert len(json_lines) == 0, f"Found JSON log lines in stdout (verbose): {json_lines}"
        
        # Check stderr HAS JSON log lines (verbose mode - logs go to stderr)
        stderr_lines = stderr.strip().split('\n')
        stderr_json_lines = [l for l in stderr_lines if l.startswith('{') and '"timestamp"' in l]
        assert len(stderr_json_lines) > 0, f"Expected JSON log lines in stderr (verbose), got: {stderr}"
        
        # Check no "Model error" in output
        assert "Model error" not in stdout, f"Found 'Model error' in stdout: {stdout}"
        
        # Check no "Insufficient" in output
        assert "Insufficient" not in stdout, f"Found 'Insufficient' in stdout: {stdout}"
        
        assert "KANGAROO" in stdout.upper(), f"Expected 'KANGAROO' in response, got: {stdout}"
        
        assert result.returncode == 0, f"CLI exited with code {result.returncode}, stderr: {stderr}"
        
        # Verify logs went to temp file with correct session_id
        with open(isolated_log_env["log_file"], "r") as f:
            log_lines = f.readlines()
        assert len(log_lines) > 0, "Expected log entries in temp log file"
        for line in log_lines:
            assert f'"session_id": "{isolated_log_env["session_id"]}"' in line, f"Log missing session_id: {line}"


if __name__ == "__main__":
    # Allow running directly for debugging
    pytest.main([__file__, "-v", "-s"])