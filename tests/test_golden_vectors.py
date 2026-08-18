"""链上事实：这里的每一行都是已经发生过的评测，改一行就是改历史。

数据来源：生产 PostgreSQL 的导出
``openroboto-backend/tests/fixtures/prod-data.sql``（round 1，netuid 80），
取自 ``submissions`` 与 ``submissions_master`` 两表中 seed / block_hash /
drand_random 三列齐全的记录，按 (block_hash, round_num, drand_random) 去重。

其中的 drand randomness 已对公开信标做过抽样复核（round 6347967 / 6370589
与 https://api.drand.sh 返回的 randomness 逐字一致），确认它们确实是公开信标值、
不是后端自己编的数。

这个文件只有一个用途：只要有人动了 ``derive_seed`` 的行为，它立刻变红。
"""

from __future__ import annotations

import pytest

from openroboto_protocol.seed import SEED_MAX, SeedInputs, derive_seed, verify_seed

#: 可复现的历史向量：(block_hash, round_num, drand_random, seed)
#: 42 条，覆盖 round 1 全部能从存储输入复算出来的提交。
GOLDEN_SEEDS: tuple[tuple[str, int, str, int], ...] = (
    (  # uid 109 · drand round 6347967
        "0x56036890baf63e76acbc2cc7b33a4d660167e2727ceb9e3b5519c9bb2be33603",
        1,
        "fae7d29a882700d46abe155968dde013367896878a3986f2036a33fe6bd190db",
        2713118528,
    ),
    (  # uid 192 · drand round 6348128
        "0x3a918df5c2a6dd1c150ab60d25bf6eed0af7efc54b06158111287e969c2435dd",
        1,
        "6a9a17fa5a849b044af853f446a5cf820e30dbdc38619697f189249b6dc636ab",
        585831649,
    ),
    (  # uid 181 · drand round 6348872
        "0x6eb0e324a5c5bef25c12d57dacd6cf40b425d4a9607fde7a80aaf46b4bfbf7a4",
        1,
        "32c3de1340806c07534b281b4b0fe1a4c9d14975d8d18675209738825d6fe54f",
        351461779,
    ),
    (  # uid 56 · drand round 6350188
        "0xd896586dec342e2d7423e2d25f4528ce07c0b1e11bc53e468cc44b7a7a2fe112",
        1,
        "e8e79241f89e45ced3fa484b44de7032137eceb805b80d7e9b328ccbdc976236",
        3005564811,
    ),
    (  # uid 188 · drand round 6350954
        "0x5f54c91d5a4c556a0252ad893bbf126a13a18edb280d06d771d99a2ee8c0926e",
        1,
        "392803d009e91e6d30bb3b11e42139b6f0a1edef84e05e404461bf741209b245",
        2274157326,
    ),
    (  # uid 55 · drand round 6351292
        "0x46e152fb37da8580441cfeac29ecf173fc053adcd3b5bc83e9dcd83bc46fd700",
        1,
        "9279cd1a26ec37521d1358f018a6e09b5773a8bc641eb4ce29d5c7be904519d8",
        1641232181,
    ),
    (  # uid 110 · drand round 6351406
        "0xd89442a84c60f855a67d016a867602be7bd575e7f49fd7d7ec1c3d7d6cbb08f5",
        1,
        "be2a4eff0483bbfb8fc3f2dfc313a25185b88c2a6b3ac17a8e8d1552349ea1ea",
        3489427824,
    ),
    (  # uid 4 · drand round 6352042
        "0x08c08367c434ef209ebc373a8779977952e9bbf237d886822a068481e6d9abc5",
        1,
        "748375b8f12f8f0417e6b80985a6215b72ad5b782d4364bda2f06678173b6dae",
        1553764567,
    ),
    (  # uid 39 · drand round 6353075
        "0x2c7f618567844a654af7ed62ffee2dd1c580afd109328118fd6ab34596f39d75",
        1,
        "c282d86ea072d14e159bdbf74349703415f98d559c54d267d7202168eb11b0c2",
        3132927643,
    ),
    (  # uid 111 · drand round 6353578
        "0x93e155e0aab978163bd178aeabe5d0ac7ff58e76b996a916217a77544330f799",
        1,
        "18c7d4114a0acf41907d0bc60d5564ea4f8314eee2d589a958457c6e345102ae",
        405034613,
    ),
    (  # uid 199 · drand round 6354766
        "0xc0a38e48531c3e335903b75d60031e650dce31d618fd9d0775f29ce5bec64d0e",
        1,
        "069b509a1f64d0c76fafb6508646b45586d82cff00022f526162671c58678255",
        815321392,
    ),
    (  # uid 196 · drand round 6355260
        "0x7d2fd8c2e6a0a67657eb788008a0b9bb44283cc18b809fa15b2b91d0aadebbca",
        1,
        "5cab9d77392e3028135795f029b03386a82ea709e50018164dbca16e5a99a191",
        799348316,
    ),
    (  # uid 175 · drand round 6355651
        "0xed5b1b800425f64ce396c870435d1c8531f6cac1ce183aa58acd764c89485155",
        1,
        "1c41e1ef8dc94fd5f8f6fc50d5b2286e05bd059f68b0c582a396626df8755ee8",
        957734637,
    ),
    (  # uid 176 · drand round 6356139
        "0xa7fae61e0ff4219da7cc540bfe46e4ee046223036d691e15f9b89b99d9452b75",
        1,
        "5f2923cbe7ba0c6a8aa5ab96951e8c0e74da84fd07639100d4ae58658383c36b",
        521437844,
    ),
    (  # uid 200 · drand round 6357191
        "0x0e806c9398fabfa888db632171210309aca12884008c49236df7b6f302f790ab",
        1,
        "23e6248ed5ea5366e2330021cfb85913c409060f7bb0b991ae50d1da7a609fea",
        1169996343,
    ),
    (  # uid 185 · drand round 6357436
        "0xad9c4f7f23ff48a551c0327dbed369754271a2a1e251cc0f7bf885979e8d74db",
        1,
        "81fe9fc7e450d826bd79ff7716c0ebb983dfef9f50b7c292ac813ab5c1a30e05",
        763971467,
    ),
    (  # uid 194 · drand round 6358204
        "0x3a52450746fc7eada6b67c5ab12b3e728808c8ef5cda50103487346d277fa8b3",
        1,
        "a9df0fcf13a87f209a05d48904bad79b28558bc1e45b7afdb473a112d849f209",
        3713410133,
    ),
    (  # uid 70 · drand round 6361719
        "0x03561b054f17db382a1585308138bedd0470f7f8f4e42d940966521aea0c2d8e",
        1,
        "56b153c434065acaebf305e588f9dcaf8bee6fced6cc45eaafff4c3b54d44069",
        102478724,
    ),
    (  # uid 203 · drand round 6362042
        "0x548e0d28a3bee253aca5a3a960cdf9b50b4c1011a83ca2f4034848685b7a0456",
        1,
        "e651347d0e098ed2dc9c36f38d2dcc762d09aa376b8c965b5b39ac176d4f6d65",
        3105273587,
    ),
    (  # uid 7 · drand round 6362184
        "0x9b73923c81a4dadccd16d5a269a30f2561a8eda4d23a7e9675ed39e04c26cbdf",
        1,
        "0ed21ea4cae5139f464a07da9b474546133ebf2b165c368bf997e0277716a186",
        3864612630,
    ),
    (  # uid 71 · drand round 6362187
        "0x26dd38aaae34d9741347bbd6eef9594628396748301f6ed2e5d95b1a985c1b0b",
        1,
        "303bf61987c928f70ed13615b04eaf79f466ecfaa75b77be2246cadd0f9b3bdc",
        800848796,
    ),
    (  # uid 72 · drand round 6362208
        "0x05ec70cb65d88a02d1ec2f83e603ed3eb0073ac4de79667ba01474387ee981c7",
        1,
        "60b826f450899cc3e3c58b8355ef59978e69a8faebeea848dbe474f8da123e98",
        4073378716,
    ),
    (  # uid 188 · drand round 6362257
        "0x7549aa6986a6bc22d5b4fe66c72311a9d83017afc9b1ab35de57d8ee6beae4aa",
        1,
        "6bae2e3a12762e074f7f71daa29c9edc369c10a6a2741b3b17624b59764f27c4",
        3375092394,
    ),
    (  # uid 7 · drand round 6362386
        "0x97687588d5bb358dbaf9f15935d0750e109d65a92c0c1ecb59d557080b6b948e",
        1,
        "2d21ea4b9afa05857b1be5b7bb849b10faf60e4cf233d226dbc59771321a5a3b",
        1254093154,
    ),
    (  # uid 204 · drand round 6363066
        "0x2c6433cf9a37830d7cc78b4ee7e2669788f9092420790697624b4e26b77eec8c",
        1,
        "a4b4845d17498b6fd53ebbf17b38033af94143d7d533aae6cd302479a9219b83",
        623699474,
    ),
    (  # uid 205 · drand round 6363763
        "0x7620cd130a63f51a9184194d8032b596bafb10ae4379d84f20ec15a48994c352",
        1,
        "0695ab366f893bf267879a7f4767f16fe683b0f84af8da96b8122e8b10c10129",
        815078047,
    ),
    (  # uid 127 · drand round 6364709
        "0x09eb450b3389f05056cade15320831824fe3071d9eb3e9044f68ddd192322cd4",
        1,
        "f7ca1055c8d407896ebf58339f08994a5a0c96857673fd666b601bb66d37a787",
        3728404504,
    ),
    (  # uid 194 · drand round 6365376
        "0x6ddd6e41a857103f5c8f66aadfb93aac249742ca0bd83cccd97d9bb8853bf627",
        1,
        "1ca5346ad8a2cf96f4c464f05cb841792c145400dfd4fb4dab96d02ecf3ee8eb",
        1980170525,
    ),
    (  # uid 206 · drand round 6365514
        "0x8ec8c4bdcd7e6093b788b6954f1e22cba6f3da140ef6d9d85d9c3a4505e726db",
        1,
        "f7d9f6a89b7eb0be6baf1c236271c8488fb6b26929681e908423f2bb5b103438",
        2904944139,
    ),
    (  # uid 180 · drand round 6365550
        "0x73fca31e51f489b8bcf22f92d1f5675672b76e6498850fe5f00904f0da2ae539",
        1,
        "2c1a54018fc75e6d2a06d7d5c3d49ec00b42936f3e82cf6430a60bbc2954075f",
        954530127,
    ),
    (  # uid 129 · drand round 6365615
        "0xfa8a58628c27e34003d1e7a758ad82d67464e15eb9a33e9f32a0d78161530aaf",
        1,
        "8a68ff6fb0f1a28d16a81d665c5477955e104663251f07691dd61454bc39ae35",
        1875367461,
    ),
    (  # uid 207 · drand round 6366137
        "0x1bec13563d1507bf685cd99f0fdd2a9c04d91185d601f4e0f036278b551b65fc",
        1,
        "c8be95882b55cfcddf8f00dc114ba0945db8c7a0d7aca6b50697438ea809af62",
        1803649292,
    ),
    (  # uid 128 · drand round 6366248
        "0x44d899d17382499a9b78f67e6ef29dd9dbb427f5a4d3242c5889073455a942ef",
        1,
        "df530a841e31c19fe95c887e829c9f2299cf59a772c858c34118e46877e029de",
        3890176027,
    ),
    (  # uid 218 · drand round 6367753
        "0xe85dddd5116d045841883f80f98a6b696c6b0adbf19b6c78306e0012c4c24de3",
        1,
        "680a36beeb4d917056b5a31ce4c3876d755e5106a802a46023eda7465c6e6e4e",
        1064538150,
    ),
    (  # uid 196 · drand round 6367760
        "0x05e1b94956c4384225355a6319f922b9f1d4b19a2154f54acd7bf584151b6c72",
        1,
        "bb12161eaa322868eb35b3f7ca55bdc5b79e137032166efb11ebc022fd9bfc39",
        4044974728,
    ),
    (  # uid 226 · drand round 6368099
        "0x646b7628e1949b63f2c82bccad5805e0400bd7be78f3fc5cf8064ec221c8e147",
        1,
        "0a49939a9ed056645fba0f94aa1d24482afc01b550fcdcd178337c1e6bd02f6c",
        4000984200,
    ),
    (  # uid 221 · drand round 6368311
        "0xded1c2efb78bb437fb942a164221efc035360fed765e59ecb53b82e50f5c2595",
        1,
        "3d57445d0df9701603c90df1edf7b97033bfb9c2462624b76932abfdf8d735d8",
        2521506201,
    ),
    (  # uid 192 · drand round 6368475
        "0x77b34d78b47a69b21adaa9f710d344f3063f3cad946ce263da45d603bcaacd42",
        1,
        "11f5d3a08da745f1a33c55d9f1033ed79da0ddbb26df0afef0632dc697bede5e",
        1357280023,
    ),
    (  # uid 226 · drand round 6368754
        "0x2fa196c8e27a042b12e594a088f63cb0fe14f91017f9cedb9a3c18afe677c2ae",
        1,
        "7e33730bef18a8670dabdfbb40bf6cc4f467cc0a25004b4a95858fa466fec357",
        303540658,
    ),
    (  # uid 196 · drand round 6368827
        "0xa2cc81d479caea52f5504ebe1c43a711a9cee434fe38f4a8d6e625909d6277f5",
        1,
        "267440ac0bd2496284178b192cfc942f9f1c5b57f861dd9b1f36b35541edb122",
        1575832882,
    ),
    (  # uid 130 · drand round 6369651
        "0x0bb9f160ddbc8b55806715411b32626233fe812f0ff8f0e8402e870d406b4592",
        1,
        "e16529365dec12455fd9730ccdab44361897c750d218b6e5fd96c1d46598fd51",
        4031553442,
    ),
    (  # uid 87 · drand round 6370589
        "0xb0dabaafd82dd55c8fe2d34435dd0312549ede90b5d2949d42017df8fcd37223",
        1,
        "1449ee97c6aa5fbba6b7d319a83b1e3c13db3ed0959b8b97f7dc19f74c992708",
        2032668320,
    ),
)

#: ⚠️ 不可复现清单
#: (block_hash, round_num, drand_random, 库里存的 seed, 用存储输入复算出的 seed)
#:
#: 这 3 条（uid 60 / 194 / 192）**不是**黄金向量，它们进不了 GOLDEN_SEEDS。
#: 已核实的事实只有一条：库里存的 seed 与**库里存的那份输入**算不出来。
#: 也就是说，当初参与派生的输入和最终落库的那份不是同一份，真实输入已经没了。
#: 具体是哪一步覆盖了输入，本次没有查到证据，不在这里写猜测。
#: 已获认可：不追认、不改数据、不塞进黄金向量 —— 否则测试永远红。
#:
#: 它们留在这里当**回归哨兵**：复算结果被钉死，任何人动了 derive_seed，
#: 这三条和上面 42 条会同时响。
UNREPRODUCIBLE_SEEDS: tuple[tuple[str, int, str, int, int], ...] = (
    (  # uid 194 · drand round 6351121
        "0xab4d6be1f17d75ff33671881b3281f14fac725f3d3a73bff5cd5b3401b3bdb6c",
        1,
        "0815bb6105d3c820bade71a6f09e7ef88aed742cc03dcdcae0bb0c6bcf77861a",
        500840540,
        637177768,
    ),
    (  # uid 60 · drand round 6352664
        "0xd27a19381ed92441e604c5d546202766b9074da072614f5cae4f02be2c7c7bce",
        1,
        "2d5c433f053e7f640604fa5cc3c897ab330feacaf5123657c87c7fef49940f53",
        363223716,
        633486020,
    ),
    (  # uid 192 · drand round 6362186
        "0x3d700dbb0aca9f41741de0f2fb9dd24a7f399431a7d9c71c0d4f740fbb3f23f0",
        1,
        "5593285afc66bf8160e17134b42165197b81bed8275a55bcd9102a8c87ebccd7",
        3076237771,
        513002069,
    ),
)


@pytest.mark.parametrize(
    ("block_hash", "round_num", "drand_random", "seed"), GOLDEN_SEEDS
)
def test_golden_seed_is_reproducible(
    block_hash: str, round_num: int, drand_random: str, seed: int
) -> None:
    """链上发生过的每一条评测，今天必须还能算出同一个 seed。"""
    assert derive_seed(block_hash, round_num, drand_random) == seed
    assert verify_seed(seed, block_hash, round_num, drand_random)
    assert SeedInputs(block_hash, round_num, drand_random).verify(seed)


@pytest.mark.parametrize(
    ("block_hash", "round_num", "drand_random", "seed"), GOLDEN_SEEDS
)
def test_golden_seed_is_uint32(
    block_hash: str, round_num: int, drand_random: str, seed: int
) -> None:
    """历史 seed 全部落在 uint32 值域内 —— 存 seed 的列必须是 BIGINT 才装得下。"""
    assert 0 <= seed <= SEED_MAX


def test_golden_vector_set_is_intact() -> None:
    """向量条数与去重性本身也是契约：少一条就是有人删了一段历史。"""
    assert len(GOLDEN_SEEDS) == 42
    keys = {(bh, rn, dr) for bh, rn, dr, _ in GOLDEN_SEEDS}
    assert len(keys) == len(GOLDEN_SEEDS)


@pytest.mark.parametrize(
    ("block_hash", "round_num", "drand_random", "stored_seed", "derived_seed"),
    UNREPRODUCIBLE_SEEDS,
)
def test_unreproducible_seed_stays_unreproducible(
    block_hash: str,
    round_num: int,
    drand_random: str,
    stored_seed: int,
    derived_seed: int,
) -> None:
    """这 3 条已知对不上。复算结果被钉死 —— 变了说明公式被动过。"""
    assert derive_seed(block_hash, round_num, drand_random) == derived_seed
    assert derived_seed != stored_seed
    assert not verify_seed(stored_seed, block_hash, round_num, drand_random)


def test_unreproducible_list_is_exactly_three() -> None:
    """已获认可的例外就这 3 条。多一条就是又出了一次同样的事故，必须有人看见。"""
    assert len(UNREPRODUCIBLE_SEEDS) == 3
    assert not {(bh, rn, dr) for bh, rn, dr, _, _ in UNREPRODUCIBLE_SEEDS} & {
        (bh, rn, dr) for bh, rn, dr, _ in GOLDEN_SEEDS
    }
