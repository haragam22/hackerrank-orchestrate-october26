from sim.run_sim import generate_users


def test_users_are_seeded_and_reproducible():
    assert generate_users(200, 7) == generate_users(200, 7)
    assert generate_users(200, 7) != generate_users(200, 8)


def test_user_mix_is_roughly_70_20_10():
    kinds = [k for k, _ in generate_users(2000, 2026)]
    share = {k: kinds.count(k) / len(kinds) for k in ("valid", "edge", "invalid")}
    assert 0.66 < share["valid"] < 0.74 and 0.17 < share["edge"] < 0.23 and 0.08 < share["invalid"] < 0.12


def test_package_excludes_sim_outputs(tmp_path):
    from package import build
    code = tmp_path / "code"
    (code / "sim" / "out").mkdir(parents=True)
    (code / "sim" / "out" / "results.jsonl").write_text("x", encoding="utf-8")
    (code / "sim" / "run_sim.py").write_text("x", encoding="utf-8")
    assert build(tmp_path / "c.zip", repo_root=tmp_path, code_dir=code) == ["code/sim/run_sim.py"]
