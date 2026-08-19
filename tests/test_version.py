"""版本号与 README 里的安装示例必须是同一个数。

为什么值得一条测试：这个包唯一的卖点是"两边能被证明装的是同一份"，而消费方
照抄的就是 README 里那行 `uv add "openroboto-protocol==X"`。README 写 1.0.0、
`pyproject.toml` 还停在 0.1.0 的时候，抄过去的人装不上；更糟的是版本 bump 之后
只改了 `pyproject.toml`，README 继续把所有新消费方钉在一个老版本上 ——
没有任何东西会报错，只会在某天发现两边跑的不是一套代码。

版本从**已安装的发行版元数据**读，不是从 `pyproject.toml` 文本里 grep：
消费方 `pip install` 之后看到的就是这个值。
"""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"

# 匹配 `openroboto-protocol==1.0.0`，以及带 extra 的
# `openroboto-protocol[schemas]==1.0.0`。版本段要求以数字开头，所以 CI 片段里
# 那句 `grep -v 'openroboto-protocol=='`（`==` 后面没有版本号）不会被误当成钉版。
_PIN = re.compile(r"openroboto-protocol(?:\[[^\]]*\])?==([0-9][^\"'\s`)]*)")


def test_readme_pins_match_the_installed_version() -> None:
    """README 里出现的每一处精确钉版都必须等于本包版本。"""
    pins = _PIN.findall(README.read_text(encoding="utf-8"))
    assert pins, "README 里一处 `openroboto-protocol==<版本>` 都没有，安装示例丢了"
    assert set(pins) == {version("openroboto-protocol")}, (
        f"README 钉的版本 {sorted(set(pins))} 与包版本 "
        f"{version('openroboto-protocol')} 不一致"
    )


def test_version_and_the_compatibility_promise_move_together() -> None:
    """版本号和"承不承诺兼容"这件事必须同进同退，不许一边改一边不改。

    ⚠️ 这条不是版本号格式检查，是**裁决的执行者**。

    原来它断言的是 `major >= 1`（"契约包不能以 0.x 发布"）。2026-08-19 改了：
    0.x 允许，但**必须在文档里说明这期间不承诺兼容**。理由是
    `openroboto-backend` 和 `openroboto-cli` 还没有确定各自的上线版本 ——
    今天它俩甚至装不上这个包（backend 还留着 3 处手抄副本）。
    给一份从没被真正消费过的契约冻结版本号，冻住的是它**碰巧长成的**形状。

    所以这条现在守两个方向，两个都是静默出错的形状：

    1. **0.x 却没有警告** —— 消费方按 SemVer 直觉以为 `0.1.x` 之间兼容，
       而我们随时可能改 `schemas.py` 的形状。没人会报错，只会解析出错的数据。
    2. **1.0+ 还留着警告** —— 契约已经冻结了，文档却还写着"不承诺兼容"，
       于是消费方不敢钉版本、继续手抄 —— 正是这个包要消灭的东西。

    `1.0.0` 发出去之后就不许再退回 0.x：那等于把已经生效的承诺收回，
    而消费方那边 `==` 钉着的版本不会自己知道这件事。
    """
    root = Path(__file__).resolve().parents[1]
    major = int(version("openroboto-protocol").split(".")[0])
    warned = "compatibility is not promised" in (root / "README.md").read_text(
        "utf-8"
    ) and "不承诺兼容" in (root / "AGENTS.md").read_text("utf-8")
    assert _promise_mismatch(major, warned) is None, _promise_mismatch(major, warned)


def _promise_mismatch(major: int, warned: bool) -> str | None:
    """判据抽成纯函数，好让**两个方向**都测得到。

    直接写 `if major == 0: assert … else: assert …` 的话，另一半永远跑不到
    （今天版本是 0.x），覆盖率 100% 的门槛会红 —— 而"把那一半标成 no cover"
    等于承认这条规则有一半从来没被验证过。而它守的正是**将来**那一半：
    1.0 发出去、文档忘了把警告拿掉。
    """
    if major == 0 and not warned:
        return (
            "版本是 0.x，但 README / AGENTS.md 里没有「这期间不承诺兼容」的警告"
            " —— 消费方会按 SemVer 直觉当它兼容"
        )
    if major > 0 and warned:
        return (
            f"版本已经是 {major}.x（契约生效），文档却还写着不承诺兼容"
            " —— 消费方会继续手抄而不是钉版本"
        )
    return None


@pytest.mark.parametrize(
    ("major", "warned", "bad"),
    [
        (0, True, False),  # 今天：0.x + 有警告
        (0, False, True),  # 0.x 却没警告 —— 消费方会当它兼容
        (1, False, False),  # 1.0 之后：契约生效、警告已拿掉
        (1, True, True),  # 1.0 了还留着警告 —— 消费方不敢钉版本，继续手抄
        (2, True, True),  # major 再往上同理
    ],
)
def test_promise_mismatch_catches_both_directions(
    major: int, warned: bool, bad: bool
) -> None:
    """两个方向各两条。少哪一条，那个方向的静默错误就没人守。"""
    assert (_promise_mismatch(major, warned) is not None) is bad
