import asyncio, os, tempfile
import claude_bash as cb

async def main():
    workdir = tempfile.mkdtemp()
    state = cb.EngineState(initial_cwd=workdir)
    tool = cb.BashTool(state)  # real snapshot path (NOT skipped)

    # 1. basic
    rr = await tool.run("echo hello-world", cb.AbortContext())
    assert rr.exec_result.code == 0 and "hello-world" in rr.exec_result.stdout
    print("1 basic:", rr.exec_result.stdout.strip())

    # 2. cwd tracking via real snapshot+eval pipeline
    target = os.path.realpath("/tmp")
    rr = await tool.run(f"cd {target} && pwd", cb.AbortContext())
    assert state.cwd == target, (state.cwd, target)
    print("2 cwd tracked ->", state.cwd)

    # 3. heredoc
    rr = await tool.run("cat <<EOF\nh1\nh2\nEOF", cb.AbortContext())
    assert "h1" in rr.exec_result.stdout and "h2" in rr.exec_result.stdout
    print("3 heredoc OK")

    # 4. pipe (rearranged stdin redirect)
    rr = await tool.run("printf 'a\\nb\\nc\\n' | wc -l", cb.AbortContext())
    assert rr.exec_result.stdout.strip() == "3"
    print("4 pipe ->", rr.exec_result.stdout.strip())

    # 5. large output -> persisted file
    rr = await tool.run("for i in $(seq 1 6000); do echo line$i; done", cb.AbortContext())
    assert rr.exec_result.output_file_path and rr.persisted_output_path
    print("5 large output persisted:", rr.persisted_output_size, "bytes ->", os.path.basename(rr.persisted_output_path))

    # 6. timeout -> SIGTERM, no orphan
    rr = await tool.run("sleep 30", cb.AbortContext(), timeout=400)
    assert rr.exec_result.code == 143 and "timed out" in rr.exec_result.stderr.lower()
    print("6 timeout:", rr.exec_result.stderr.strip())

    # 7. background + incremental poll + kill, via raw exec()
    abort = cb.AbortContext()
    sc = await cb.exec("for i in $(seq 1 50); do echo b$i; sleep 0.05; done", abort, "bash", state)
    await asyncio.sleep(0.15)
    sc.background("smoke-bg")
    await asyncio.sleep(0.2)
    chunk, off = await cb.get_task_output_delta(sc.task_output.path, 0)
    assert "b" in chunk, chunk
    print("7 background poll got:", chunk.split(chr(10))[0], "... offset", off)
    sc.kill()
    res = await sc.result
    assert res.background_task_id == "smoke-bg" and res.code == 137
    print("7 background killed, code", res.code)

    # 8. grep semantics (exit 1 = no match, not error)
    rr = await tool.run("echo hi | grep nomatch", cb.AbortContext())
    assert rr.exec_result.code == 1 and rr.return_code_interpretation == "No matches found"
    print("8 grep semantics:", rr.return_code_interpretation)

    print("\nALL SMOKE CHECKS PASSED")

asyncio.run(main())
