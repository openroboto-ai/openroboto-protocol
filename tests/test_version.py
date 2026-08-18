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


def test_version_is_a_released_contract_version() -> None:
    """0.x 在 SemVer 下不承诺兼容性，而这个包的全部意义就是承诺兼容性。

    ⚠️ 这条不是版本号格式检查，是"契约已经冻结"这件事的断言。
    真要退回 0.x 发布，先改 README 的 Versioning 一节再改这里。
    """
    major = version("openroboto-protocol").split(".")[0]
    assert int(major) >= 1, "契约包不能以 0.x 发布：0.x 等于对兼容性不作承诺"
