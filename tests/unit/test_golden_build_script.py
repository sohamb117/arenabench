from pathlib import Path


def test_build_checks_customized_image_before_finalizing() -> None:
    script = Path("vm/golden/build.sh").read_text(encoding="utf-8")

    check_index = script.index('qemu-img check "$GOLDEN_PATH.tmp"')
    move_index = script.index('mv "$GOLDEN_PATH.tmp" "$GOLDEN_PATH"')

    assert check_index < move_index
