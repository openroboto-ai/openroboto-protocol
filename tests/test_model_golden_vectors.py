"""Golden vectors: model fingerprints and repo structures that really happened on
chain. Changing one of them means changing history.

All three fixtures are HuggingFace repo trees of production submissions, fetched
on 2026-08-17 from
``https://huggingface.co/api/models/<repo>/tree/<commit>?recursive=true``, where
``<commit>`` is the very revision announced on chain and recorded in the
production DB:

- ``UID221_PYTORCH_TREE`` = uid 221 ``joseneto023dev/pi05-BRVeJ37DuryX@636dbaa3``
  — one of the two submissions falsely rejected on 2026-08-14, a flat openpi
  PyTorch checkpoint;
- ``UID181_JAX_TREE`` = uid 181 ``OpenRd/pi05-vLUxPb8qTmGv@97da4275``
  — the repo root is itself a JAX orbax checkpoint;
- ``UID130_NESTED_JAX_TREE`` = uid 130 ``swordswoman/pi05-j71bm5DVdD5X@ed38d896``
  — 10.8 GB, with the whole tree nested under ``merged/``.

The four items ``type`` / ``size`` / ``path`` / ``lfs.oid`` are kept verbatim;
the git blob ``oid`` and ``xetHash``, which the verdict does not use, were
dropped.

Where the expected fingerprints come from
-----------------------------------------
Every ``*_MODEL_HASH`` is the value stored in ``submissions.model_hash`` in the
**production PostgreSQL dump**
(``openroboto-backend/tests/fixtures/prod-data.sql``, round 1); it is not
something we computed just now.

While extracting this module, all 37 submissions in the dump whose "hf_commit is
a full 40-character sha" were run through: **35 reproduced verbatim, 0
mismatched**, and the revisions of the other 2 have disappeared from HF
(``joseneto023dev/pi05-6YM1X5xNCoQZ@a252578b``,
``joseneto023dev/pi05-aAxJBYQz5qqu@1a54a589`` → ``Invalid rev id``). Those 2
**must not** go into the golden vectors — the inputs are gone, so the test would
be red forever.

All three trees passed production admission and entered the evaluation queue, so
there is only one possible meaning when a layout case goes red: the new rule
would reject real miners, and must not ship.

The last two trees are of a different kind
------------------------------------------
``LINGBOT_REFERENCE_TREE`` and ``LINGBOT_POST_TRAINED_TREE`` are **not**
production submissions — no LingBot-VLA round has run yet, and by the time one
has, the rules that would have to be right on day one are already shipped. They
are the vendor's two published checkpoints, the only real LingBot trees that
exist today: the base model, and the RoboTwin post-trained artifact miners are
shown as an example of a finished model. Neither carries an expected
``model_hash``: no on-chain fingerprint for them exists, and inventing one would
be the opposite of what a golden vector is.

What the LingBot cases pin is **mutual exclusion** plus **the cost of copying
the example**: the openpi rules keep accepting the three real openpi repos,
neither set of rules accepts the other side's tree, and the post-trained
artifact — uploaded unchanged — is admitted with exactly one warning, the one
that says the evaluator will not find the weights. Losing the first half means
every existing miner is rejected on the day the new base model ships; losing the
last one means they burn first and find out afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openroboto_protocol.model_format import (
    LINGBOT_MODEL_CONFIG_FILE,
    LINGBOT_WEIGHTS_INDEX_FILE,
    CheckpointFile,
    CheckpointKind,
    FormatIssueCode,
    LingbotLayout,
    check_checkpoint_layout,
    check_lingbot_layout,
)
from openroboto_protocol.model_hash import model_hash_from_hf_tree


def _tree(
    *entries: tuple[str, int, str] | tuple[str, int, str, str],
) -> list[dict[str, Any]]:
    """Rebuild (type, size, path[, lfs oid]) into the entry shape of the HF tree
    API."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        node: dict[str, Any] = {"type": entry[0], "size": entry[1], "path": entry[2]}
        if len(entry) == 4:
            node["lfs"] = {"oid": entry[3], "size": entry[1], "pointerSize": 135}
        out.append(node)
    return out


def _files(tree: list[dict[str, Any]]) -> list[CheckpointFile]:
    """HF tree → the input of the layout verdict. This glue is deliberately left
    in the caller and does not go into the protocol package."""
    return [
        CheckpointFile(path=e["path"], size_bytes=e["size"])
        for e in tree
        if e["type"] == "file"
    ]


UID221_PYTORCH_TREE = _tree(
    ("directory", 0, "assets"),
    ("directory", 0, "assets/physical-intelligence"),
    ("directory", 0, "assets/physical-intelligence/libero"),
    ("file", 1519, ".gitattributes"),
    ("file", 1943, "assets/physical-intelligence/libero/norm_stats.json"),
    ("file", 149, "config.json"),
    (
        "file",
        7233650272,
        "model.safetensors",
        "86cb3c2a3ac8ade1640f7fa1657d85df7cf4560cc436626e56c4173d6c9b9809",
    ),
    ("file", 119, "round_info.json"),
)
UID221_MODEL_HASH = "cf9fb4ba2504e35b6120221e042fd0d565f44a7bde5e741c3edcbbb008564e53"

UID181_JAX_TREE = _tree(
    ("directory", 0, "assets"),
    ("directory", 0, "assets/physical-intelligence"),
    ("directory", 0, "assets/physical-intelligence/libero"),
    ("directory", 0, "params"),
    ("directory", 0, "params/d"),
    ("directory", 0, "params/ocdbt.process_0"),
    ("directory", 0, "params/ocdbt.process_0/d"),
    ("file", 1895, ".gitattributes"),
    ("file", 1986, "assets/physical-intelligence/libero/norm_stats.json"),
    ("file", 258, "params/_CHECKPOINT_METADATA"),
    ("file", 22089, "params/_METADATA"),
    ("file", 0, "params/commit_success.txt"),
    ("file", 2013, "params/d/0205a790fca0d61a156f7d1997925e38"),
    ("file", 117, "params/manifest.ocdbt"),
    (
        "file",
        2719569594,
        "params/ocdbt.process_0/d/01af9b532500fa152fac66d1a477ef7e",
        "f559903aa654e1f20f73ef794cf6016b38831988c2df746e4ab91c79addd986b",
    ),
    ("file", 1999, "params/ocdbt.process_0/d/0240f6141c4100b81b8936b22cf83715"),
    ("file", 1137, "params/ocdbt.process_0/d/06b01aefbf04c9ba54b0ff7dfaa6bd4d"),
    ("file", 213, "params/ocdbt.process_0/d/46c872851604dd25b33c7b4d8c39f50a"),
    (
        "file",
        1939700553,
        "params/ocdbt.process_0/d/607549e7fe926d83a32d981fc19a54ab",
        "e309d2944a4f53ec959cfc13dc67f710ea1ed6e25fb0fd211ab6ef79d7833445",
    ),
    (
        "file",
        1212428545,
        "params/ocdbt.process_0/d/8b1877650f65a92ff95fc5a83fc30528",
        "652d0fe72afde40176bb2eb3481d56b3fe804f4cdf02db6f38b1d02e282b130a",
    ),
    (
        "file",
        277718891,
        "params/ocdbt.process_0/d/ac23fd467df222951dcb69b7136b4e4f",
        "e2665687c1fbfd54db9d4998e9f55a03f91e507fe3ff90d2ee89cee65834c333",
    ),
    ("file", 5407, "params/ocdbt.process_0/d/e3dd7fb4fa35901dac286f41adf77028"),
    ("file", 402, "params/ocdbt.process_0/manifest.ocdbt"),
    ("file", 118, "round_info.json"),
)
UID181_MODEL_HASH = "81f3865269675fceebf14c6f81f4b10d9fff82d3f5dbe91c75719df8c7747340"

UID130_NESTED_JAX_TREE = _tree(
    ("directory", 0, "merged"),
    ("directory", 0, "merged/assets"),
    ("directory", 0, "merged/assets/physical-intelligence"),
    ("directory", 0, "merged/assets/physical-intelligence/libero"),
    ("directory", 0, "merged/params"),
    ("directory", 0, "merged/params/d"),
    ("directory", 0, "merged/params/ocdbt.process_0"),
    ("directory", 0, "merged/params/ocdbt.process_0/d"),
    ("file", 2327, ".gitattributes"),
    ("file", 1943, "merged/assets/physical-intelligence/libero/norm_stats.json"),
    ("file", 258, "merged/params/_CHECKPOINT_METADATA"),
    ("file", 19947, "merged/params/_METADATA"),
    ("file", 2087, "merged/params/d/ef46ae811f00c73de5d9d8b6aca73392"),
    ("file", 117, "merged/params/manifest.ocdbt"),
    (
        "file",
        370577010,
        "merged/params/ocdbt.process_0/d/0496c7a9d125a9817f7a9c9bd93eb17d",
        "ea597b71001a483a1256f2efd3222c36c9baeb34a26e7fe3d6726e61cb04b911",
    ),
    (
        "file",
        1514498265,
        "merged/params/ocdbt.process_0/d/0d82ccfe6c60fbb664bf0ae1404a9c6d",
        "5b89a92ac765014d47d3cdc4cac3c2f1b2ac1b611d98c48f1de40b1db4385347",
    ),
    ("file", 209, "merged/params/ocdbt.process_0/d/5b49c31915efcb433684e2f31fc98042"),
    (
        "file",
        2175364316,
        "merged/params/ocdbt.process_0/d/5b5aeb6f7029c44cce42ccc91fcf9d73",
        "f0e46ac235a9f79d9d3d1a6d068b0e603b4171df1f0abf76c502f736dbe09d11",
    ),
    (
        "file",
        2175513663,
        "merged/params/ocdbt.process_0/d/67713312ad62886e4bde7d2eb230e256",
        "5568fb74fae5cdf11d04a0da2cdbec129b6a9dcbe373f77421fbdc7ddaa083a8",
    ),
    (
        "file",
        1096331571,
        "merged/params/ocdbt.process_0/d/72ef75981911f367ac3879ed66842552",
        "b9d447d117215bc6ed3bef14efcdbea04a44bf3c953111d14c43b7fa3ae3a465",
    ),
    ("file", 1077, "merged/params/ocdbt.process_0/d/96302abed73d3129da964812a39eb0ee"),
    (
        "file",
        28791290,
        "merged/params/ocdbt.process_0/d/a03d5d3697936b091cf3893deb91bb52",
        "59d747ccf592623c8d402c5b0e5dc2a8471a6475d3e8817140d6145c385516a1",
    ),
    (
        "file",
        2175351961,
        "merged/params/ocdbt.process_0/d/bf25d95dc975ca20e05ddc679a50d86b",
        "b52c9ba6a59de7c9404289b561107e694434470abe15d60bbf47895fed285855",
    ),
    (
        "file",
        1818585087,
        "merged/params/ocdbt.process_0/d/c3d075dee73a5d3f4abecff5459a7351",
        "7f4f42c1939f49fc723a797c6e75645e142c6763fd79a53f2c4b6ecbe4080019",
    ),
    ("file", 5344, "merged/params/ocdbt.process_0/d/e5a55319d098d80140fe2caa774dbd74"),
    ("file", 2044, "merged/params/ocdbt.process_0/d/f494f578b232cd178835516db312f5a9"),
    ("file", 489, "merged/params/ocdbt.process_0/manifest.ocdbt"),
    ("file", 5, "merged/round_info.json"),
    ("file", 118, "round_info.json"),
)
UID130_MODEL_HASH = "d5ba20f63ec661af87f2a5c5c92ea8644f88f5b063dd5f30bd060d477d36ac5c"


# ── Fingerprints: verbatim identical to submissions.model_hash stored in the
#    production DB ──────────────────────────────────────────────────────────


def test_uid221_model_hash() -> None:
    """A single LFS weights file. HF only gives ``lfs.oid``, not
    ``lfs.sha256``."""
    assert model_hash_from_hf_tree(UID221_PYTORCH_TREE) == UID221_MODEL_HASH


def test_uid181_model_hash() -> None:
    """4 LFS shards; the same directory also holds 6 small non-LFS files, which
    do not go into the fingerprint."""
    assert model_hash_from_hf_tree(UID181_JAX_TREE) == UID181_MODEL_HASH


def test_uid130_model_hash() -> None:
    """8 LFS shards — the sort-and-join step can only be verified with multiple
    files."""
    assert model_hash_from_hf_tree(UID130_NESTED_JAX_TREE) == UID130_MODEL_HASH


def test_model_hash_is_independent_of_listing_order() -> None:
    """The same weights in a different upload/return order must give the same
    fingerprint, otherwise re-uploading once would launder plagiarism."""
    assert (
        model_hash_from_hf_tree(list(reversed(UID130_NESTED_JAX_TREE)))
        == UID130_MODEL_HASH
    )


def test_model_hash_ignores_non_lfs_files() -> None:
    """Editing metadata such as ``round_info.json`` does not change the
    fingerprint — a plagiarist cannot escape by editing metadata."""
    tampered = [
        {**e, "size": e["size"] + 1} if e["path"].endswith("round_info.json") else e
        for e in UID130_NESTED_JAX_TREE
    ]
    assert model_hash_from_hf_tree(tampered) == UID130_MODEL_HASH


def test_different_weights_give_different_model_hash() -> None:
    """The three repos are pairwise different — a fingerprint collision means a
    plagiarism verdict, and colliding with the wrong person costs somebody else
    their money."""
    assert len({UID221_MODEL_HASH, UID181_MODEL_HASH, UID130_MODEL_HASH}) == 3


# ── Layout admission: all three repos were let through in production ──────


def test_uid221_layout_accepted() -> None:
    """The submission falsely rejected on 2026-08-14 must pass.
    ``.gitattributes`` is generated automatically by HF."""
    report = check_checkpoint_layout(_files(UID221_PYTORCH_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.PYTORCH
    assert report.warnings == ()
    assert report.counted_size_bytes == 1943 + 149 + 7233650272 + 119


def test_uid181_layout_accepted() -> None:
    report = check_checkpoint_layout(_files(UID181_JAX_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.JAX
    assert report.warnings == ()


def test_uid130_nested_layout_accepted() -> None:
    """The whole checkpoint is nested under ``merged/``, and it is still a legal
    submission.

    Nested by 1 level, within the 2 levels the evaluator can search, so there
    should be no warning.
    """
    report = check_checkpoint_layout(_files(UID130_NESTED_JAX_TREE))
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.JAX
    assert report.warnings == ()


def test_real_repo_without_norm_stats_is_rejected() -> None:
    """The same real repo must be rejected once norm_stats is removed —
    admission is not a formality."""
    stripped = [
        f for f in _files(UID221_PYTORCH_TREE) if not f.path.endswith("norm_stats.json")
    ]
    report = check_checkpoint_layout(stripped)
    assert not report.ok
    assert [i.code for i in report.errors] == [FormatIssueCode.MISSING_NORM_STATS]


# ── LingBot-VLA 2.0: the vendor's reference checkpoint ────────────────────
#
# Fetched from
# https://huggingface.co/api/models/robbyant/lingbot-vla-v2-6b/tree/<rev>?recursive=true
# repo=robbyant/lingbot-vla-v2-6b
# revision=11c703bf6a5c1f45b3b69168482da11fdbba53d7
# fetched=2026-08-25
#
# Kept in the same shape as the three trees above so the same ``_tree`` /
# ``_files`` helpers apply. Note what this tree does **not** contain: no
# ``lingbotvla_cli.yaml`` (see LingbotLayout.cli_config_file) and no
# ``norm_stats.json`` anywhere.
LINGBOT_REFERENCE_TREE = _tree(
    ("directory", 0, "assets"),
    ("directory", 0, "depth"),
    ("directory", 0, "dino_video"),
    ("file", 1797, ".gitattributes"),
    ("file", 2227, "README.md"),
    ("file", 707, "added_tokens.json"),
    (
        "file",
        1178043,
        "assets/lingbot_vla2_framework.png",
        "1dbbf05745216032dad897f6f5e59d02153325e1d39cfc01329b85e2ea2e41e7",
    ),
    (
        "file",
        445663,
        "assets/lingbot_vla2_loss_mse_comparison.png",
        "95108dee5d57842d18be27bf92424123b823449567f3d60cefbbac59379ffedd",
    ),
    (
        "file",
        2317830,
        "assets/lingbot_vla2_vis_distillation.png",
        "40ce6810ea47ab944de030551efd7753fb2ed84bd7477d3668ba0fab84c3277e",
    ),
    ("file", 31, "config.json"),
    (
        "file",
        1316220456,
        "depth/model.pt",
        "d70c5191eab853d436763b35d40ff99d13534b4bcd43e4d02823656968159e5b",
    ),
    ("file", 1388, "dino_video/config.yaml"),
    (
        "file",
        1401509792,
        "dino_video/teacher_step_10000.pth",
        "086285efd8d65bc66e96b363807c4010ee5c790b7452b765edaa23837a63705b",
    ),
    (
        "file",
        4987151072,
        "model-00001-of-00006.safetensors",
        "4afb52b06a13df8b738a156ae5c8196d3bfe6b3ca931cecbd701e44cb9674e45",
    ),
    (
        "file",
        4985113408,
        "model-00002-of-00006.safetensors",
        "ec131afa26a340db94c0dba8ec00e990be5f3d842ce6532070f0c8e26a067501",
    ),
    (
        "file",
        4928593216,
        "model-00003-of-00006.safetensors",
        "7dccb068ca66c11fa514476d64661eeead56e2e80e1c0572c9ea82aa0d9ecf27",
    ),
    (
        "file",
        4990740540,
        "model-00004-of-00006.safetensors",
        "1c2cb78066b69ae11255db851df95071e2cd69a3a4bc5020b3fd3b13b17819fb",
    ),
    (
        "file",
        4990095864,
        "model-00005-of-00006.safetensors",
        "8fe36bf1f4f617869954bdfa1ad12e16abd8b0235a5fa99c88d571d4cddf4a17",
    ),
    (
        "file",
        622195024,
        "model-00006-of-00006.safetensors",
        "3cf613d592dad64b1e2a1b1bb34a6f556a7fa05eacdb9972d73e0ea4882555a0",
    ),
    ("file", 207389, "model.safetensors.index.json"),
    ("file", 782, "preprocessor_config.json"),
    ("file", 613, "special_tokens_map.json"),
    (
        "file",
        11422654,
        "tokenizer.json",
        "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    ),
    ("file", 5472, "tokenizer_config.json"),
    ("file", 817, "video_preprocessor_config.json"),
    ("file", 2776833, "vocab.json"),
)

# ── The vendor's official post-trained artifact ───────────────────────────
#
# repo=robbyant/lingbot-vla-v2-6b-robotwin  published=2026-07-24  listed=2026-08-25
# revision=0451855729ec904f970600e0aec8b84661423afe
#
# 🔴 This is the tree a miner copies. The vendor fine-tuned the base model above
# on RoboTwin and published the result **as the training script wrote it**:
# ``lingbotvla_cli.yaml`` alone at the repo root, and the whole HF checkpoint
# three levels down under ``checkpoints/global_step_50000/hf_ckpt/``. Full
# weights over six shards (25.5 GB), not an adapter.
#
# Provenance: type / size / path / lfs.oid are that revision's listing verbatim,
# fetched the same way as the four trees above. The oids were **missing from
# this fixture until 2026-08-25** and were filled in by the fingerprint audit —
# they are what makes the two LingBot trees comparable, and the comparison is
# the point (see the fingerprint cases at the bottom of this file). The tree
# still carries no expected ``model_hash``: no on-chain fingerprint for it
# exists, and inventing one would be the opposite of what a golden vector is.
_HF_CKPT = "checkpoints/global_step_50000/hf_ckpt"

LINGBOT_POST_TRAINED_TREE = _tree(
    ("file", 1678, ".gitattributes"),
    ("file", 2752, "README.md"),
    (
        "file",
        234893,
        "assets/lingbot_vla2_framework.png",
        "5e7fccb501606ae27383bbb0c5e6d6824a5cdcaa9a2534488b5af059bb7e13d7",
    ),
    ("file", 707, f"{_HF_CKPT}/added_tokens.json"),
    ("file", 5292, f"{_HF_CKPT}/chat_template.jinja"),
    ("file", 31, f"{_HF_CKPT}/config.json"),
    (
        "file",
        4947507248,
        f"{_HF_CKPT}/model-00001-of-00006.safetensors",
        "514801684e0f23aaa4bb64a4fd81e420b97e094331499852d5f2cc567750b360",
    ),
    (
        "file",
        4944315304,
        f"{_HF_CKPT}/model-00002-of-00006.safetensors",
        "e7b00cf59b21c2f5e9f76f7687ba423c6fe24bfa9fa8e313ef346bb081e73c9f",
    ),
    (
        "file",
        4944315360,
        f"{_HF_CKPT}/model-00003-of-00006.safetensors",
        "ace74f6d5214059b6387b5728c570a452d51b5950dd9904fe29607dbfa41447b",
    ),
    (
        "file",
        4992858968,
        f"{_HF_CKPT}/model-00004-of-00006.safetensors",
        "1f1cb6b4099e264b202eae0244f3730e1ced910c0b34355fff2a719993d0cbe5",
    ),
    (
        "file",
        4963062048,
        f"{_HF_CKPT}/model-00005-of-00006.safetensors",
        "1316d6fe054253650dc65bae416ae6cf9afa283aad82a9712b02b0dd7029394b",
    ),
    (
        "file",
        711830844,
        f"{_HF_CKPT}/model-00006-of-00006.safetensors",
        "0e1dfc6a23c43f3c387276e2389799867dfa90dc9727bb8e21250c73bfe7dd23",
    ),
    ("file", 207389, f"{_HF_CKPT}/model.safetensors.index.json"),
    ("file", 782, f"{_HF_CKPT}/preprocessor_config.json"),
    ("file", 613, f"{_HF_CKPT}/special_tokens_map.json"),
    (
        "file",
        11422654,
        f"{_HF_CKPT}/tokenizer.json",
        "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    ),
    ("file", 5472, f"{_HF_CKPT}/tokenizer_config.json"),
    ("file", 817, f"{_HF_CKPT}/video_preprocessor_config.json"),
    ("file", 2776833, f"{_HF_CKPT}/vocab.json"),
    ("file", 6263, "lingbotvla_cli.yaml"),
)

#: The six shard names of ``LINGBOT_REFERENCE_TREE``, and one real tensor name
#: per required prefix, copied out of the ``weight_map`` of that revision's
#: ``model.safetensors.index.json`` (1708 tensors in total; the whole map is not
#: needed to exercise the rules, and pasting 1708 lines would hide them).
LINGBOT_WEIGHT_MAP = {
    "model.action_in_proj.weight": "model-00006-of-00006.safetensors",
    "model.action_out_proj.weight": "model-00006-of-00006.safetensors",
    "model.state_proj.weight": "model-00006-of-00006.safetensors",
    "model.qwenvl_with_expert.qwen_expert.model.norm.weight": (
        "model-00006-of-00006.safetensors"
    ),
    "model.qwenvl_with_expert.qwenvl.model.visual.pos_embed.weight": (
        "model-00001-of-00006.safetensors"
    ),
    **{
        f"model.qwenvl_with_expert.qwenvl.model.language_model.layers.{i}."
        "self_attn.q_proj.weight": f"model-0000{i + 1}-of-00006.safetensors"
        for i in range(5)
    },
}

#: What a competition row would build. Three cameras and seven joint fields are
#: the shape of the vendor's own ``configs/vla/robotwin/robotwin.yaml``; the
#: numbers are a competition parameter, which is exactly why they are arguments
#: here and not constants in the package.
LINGBOT_LAYOUT_FIXTURE = LingbotLayout(
    model_config_file=LINGBOT_MODEL_CONFIG_FILE,
    weights_index_file=LINGBOT_WEIGHTS_INDEX_FILE,
    camera_names=("camera_top", "camera_wrist_left", "camera_wrist_right"),
    joint_field_names=("j1", "j2", "j3", "j4", "j5", "j6", "gripper"),
)


def _lingbot(tree: list[dict[str, Any]], **kwargs: Any) -> list[FormatIssueCode]:
    """Run the LingBot rules over a tree and return just the rejection codes."""
    layout = kwargs.pop("layout", LINGBOT_LAYOUT_FIXTURE)
    return [i.code for i in check_lingbot_layout(_files(tree), layout, **kwargs).errors]


def _without(tree: list[dict[str, Any]], *paths: str) -> list[dict[str, Any]]:
    return [e for e in tree if e["path"] not in paths]


# ── Mutual exclusion: the 2x2 that has to hold on the day the base model
#    changes ──────────────────────────────────────────────────────────────


def test_lingbot_reference_tree_is_accepted() -> None:
    """The vendor's own reference checkpoint must pass. If this goes red, the
    rules reject the very thing miners are told to start from."""
    report = check_lingbot_layout(
        _files(LINGBOT_REFERENCE_TREE),
        LINGBOT_LAYOUT_FIXTURE,
        weight_map=LINGBOT_WEIGHT_MAP,
    )
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.PYTORCH
    assert report.warnings == ()


def test_lingbot_post_trained_artifact_warns_only_about_nesting() -> None:
    """🔴 The official post-trained artifact, judged as it is published.

    Admitted (``ok``), recognized as ``pytorch``, and carrying exactly one
    warning: the weights are one level below what the evaluator searches. That
    combination is the worst outcome in the whole file — a submission that gets
    in, burns the TAO, takes the queue slot, and then fails at the last step,
    while the miner did nothing but upload what the vendor's own example looks
    like.

    Two things go red here, and both should:

    - dropping or renaming the nesting warning → the miner loses the only notice
      they get before paying;
    - raising ``MAX_CHECKPOINT_NESTING_DEPTH`` to 3 → the warning disappears and
      this test fails, which is correct: the constant belongs to
      ``openroboto-evaluation``, and changing our copy does not make the
      evaluator search deeper.
    """
    report = check_lingbot_layout(
        _files(LINGBOT_POST_TRAINED_TREE),
        LINGBOT_LAYOUT_FIXTURE,
        weight_map=LINGBOT_WEIGHT_MAP,
    )
    assert report.ok, report.errors
    assert report.kind is CheckpointKind.PYTORCH
    assert [w.code for w in report.warnings] == [FormatIssueCode.NESTED_TOO_DEEP]
    assert "3 levels deep" in report.warnings[0].message


def test_lifting_the_checkpoint_subdirectory_clears_the_warning() -> None:
    """The remediation miners are told to apply has to actually work.

    "Upload only ``checkpoints/global_step_N/hf_ckpt/``" is the sentence the CLI
    prints; if that tree still warned, we would be sending people through a
    25 GB re-upload for nothing.
    """
    lifted = [
        {**e, "path": e["path"].removeprefix(f"{_HF_CKPT}/")}
        for e in LINGBOT_POST_TRAINED_TREE
        if e["path"].startswith(f"{_HF_CKPT}/")
    ]
    report = check_lingbot_layout(
        _files(lifted), LINGBOT_LAYOUT_FIXTURE, weight_map=LINGBOT_WEIGHT_MAP
    )
    assert report.ok, report.errors
    assert report.warnings == ()


def test_requiring_the_cli_descriptor_contradicts_the_nesting_fix() -> None:
    """Why ``LingbotLayout.cli_config_file`` stays off by default, made
    executable.

    In the published artifact the descriptor sits at the repo root while the
    weights sit three levels below it. Turning the rule on accepts the layout
    that warns, and rejects the layout we just told the miner to upload — two
    instructions that cannot both be followed.
    """
    layout = LingbotLayout(
        model_config_file=LINGBOT_MODEL_CONFIG_FILE,
        weights_index_file=LINGBOT_WEIGHTS_INDEX_FILE,
        camera_names=LINGBOT_LAYOUT_FIXTURE.camera_names,
        joint_field_names=LINGBOT_LAYOUT_FIXTURE.joint_field_names,
        cli_config_file="lingbotvla_cli.yaml",
    )
    assert _lingbot(LINGBOT_POST_TRAINED_TREE, layout=layout) == []
    lifted = [
        {**e, "path": e["path"].removeprefix(f"{_HF_CKPT}/")}
        for e in LINGBOT_POST_TRAINED_TREE
        if e["path"].startswith(f"{_HF_CKPT}/")
    ]
    assert _lingbot(lifted, layout=layout) == [FormatIssueCode.MISSING_CLI_CONFIG]


def test_lingbot_tree_is_not_an_openpi_checkpoint() -> None:
    """Fed to the openpi rules the same tree is rejected — the two rule sets do
    not both claim the same repo."""
    report = check_checkpoint_layout(_files(LINGBOT_REFERENCE_TREE))
    assert not report.ok
    assert FormatIssueCode.MISSING_NORM_STATS in [i.code for i in report.errors]


def test_openpi_trees_are_not_lingbot_checkpoints() -> None:
    """And the other way round. ``config.json`` happens to exist in uid 221's
    repo, so what separates them is the weights layout, not one file name."""
    for tree in (UID221_PYTORCH_TREE, UID181_JAX_TREE, UID130_NESTED_JAX_TREE):
        assert FormatIssueCode.MISSING_WEIGHTS in _lingbot(tree), tree[0]["path"]


def test_openpi_trees_still_accepted_after_the_base_model_change() -> None:
    """The most expensive assertion in this file: the three repos that really
    passed admission in round 1 still pass, byte counts included. Red here means
    every existing miner is rejected on release day."""
    for tree, kind, counted in (
        (UID221_PYTORCH_TREE, CheckpointKind.PYTORCH, 1943 + 149 + 7233650272 + 119),
        (UID181_JAX_TREE, CheckpointKind.JAX, None),
        (UID130_NESTED_JAX_TREE, CheckpointKind.JAX, None),
    ):
        report = check_checkpoint_layout(_files(tree))
        assert report.ok, report.errors
        assert report.kind is kind
        assert report.warnings == ()
        if counted is not None:
            assert report.counted_size_bytes == counted


# ── One counter-example per rejection code, all derived from the real tree ─


def test_lingbot_without_cli_config() -> None:
    """Only when the competition asks for a descriptor. The reference checkpoint
    has none, which is why the default layout does not ask."""
    assert _lingbot(LINGBOT_REFERENCE_TREE) == []
    layout = LingbotLayout(
        model_config_file=LINGBOT_MODEL_CONFIG_FILE,
        weights_index_file=LINGBOT_WEIGHTS_INDEX_FILE,
        camera_names=LINGBOT_LAYOUT_FIXTURE.camera_names,
        joint_field_names=LINGBOT_LAYOUT_FIXTURE.joint_field_names,
        cli_config_file="lingbotvla_cli.yaml",
    )
    assert _lingbot(LINGBOT_REFERENCE_TREE, layout=layout) == [
        FormatIssueCode.MISSING_CLI_CONFIG
    ]


def test_lingbot_without_model_config() -> None:
    assert _lingbot(_without(LINGBOT_REFERENCE_TREE, "config.json")) == [
        FormatIssueCode.MISSING_MODEL_CONFIG
    ]


def test_lingbot_without_any_weights() -> None:
    stripped = [e for e in LINGBOT_REFERENCE_TREE if ".safetensors" not in e["path"]]
    assert _lingbot(stripped) == [FormatIssueCode.MISSING_WEIGHTS]


def test_lingbot_missing_one_shard() -> None:
    """The index names six shards, the repo holds five."""
    codes = _lingbot(
        _without(LINGBOT_REFERENCE_TREE, "model-00003-of-00006.safetensors"),
        weight_map=LINGBOT_WEIGHT_MAP,
    )
    assert codes == [FormatIssueCode.MISSING_WEIGHT_SHARD]


def test_lingbot_missing_required_tensor() -> None:
    """File names all correct, but the index has no action projection — a
    Qwen3-VL that was never turned into a VLA."""
    pruned = {k: v for k, v in LINGBOT_WEIGHT_MAP.items() if "action_in_proj" not in k}
    codes = _lingbot(LINGBOT_REFERENCE_TREE, weight_map=pruned)
    assert codes == [FormatIssueCode.MISSING_REQUIRED_TENSOR]


def test_lingbot_bare_lora() -> None:
    adapter = _tree(
        ("file", 900 * 1024 * 1024, "adapter_model.safetensors"),
        ("file", 1024, "adapter_config.json"),
        ("file", 31, "config.json"),
    )
    assert _lingbot(adapter) == [FormatIssueCode.BARE_LORA_ADAPTER]


def test_lingbot_lfs_pointers_only() -> None:
    """Every file shrunk to its pointer size: nothing is missing, but no weights
    were actually uploaded."""
    pointers = [
        {**e, "size": 135 if e["type"] == "file" else 0} for e in LINGBOT_REFERENCE_TREE
    ]
    assert _lingbot(pointers) == [FormatIssueCode.TOTAL_SIZE_TOO_SMALL]


def test_lingbot_leftover_upload_state() -> None:
    dirty = [*LINGBOT_REFERENCE_TREE, *_tree(("file", 42, ".git/config"))]
    assert _lingbot(dirty) == [FormatIssueCode.LEFTOVER_UPLOAD_STATE]


def test_lingbot_incomplete_file() -> None:
    partial = [
        *LINGBOT_REFERENCE_TREE,
        *_tree(("file", 42, "model-00001-of-00006.safetensors.tmp")),
    ]
    assert _lingbot(partial) == [FormatIssueCode.INCOMPLETE_FILE]


def test_lingbot_never_reports_openpi_only_codes() -> None:
    """The three codes tied to openpi paths must never come out of the LingBot
    rules — where LingBot keeps its normalization stats is not documented
    anywhere we could verify, so guessing a path would reject everyone."""
    openpi_only = {
        FormatIssueCode.MISSING_NORM_STATS,
        FormatIssueCode.NON_CANONICAL_NORM_STATS,
        FormatIssueCode.UNLOADABLE_WEIGHTS_FORMAT,
    }
    trees = [
        LINGBOT_REFERENCE_TREE,
        _without(LINGBOT_REFERENCE_TREE, "config.json"),
        UID221_PYTORCH_TREE,
        UID181_JAX_TREE,
    ]
    for tree in trees:
        report = check_lingbot_layout(_files(tree), LINGBOT_LAYOUT_FIXTURE)
        seen = {i.code for i in (*report.errors, *report.warnings)}
        assert not (seen & openpi_only), seen


# ── What the fingerprint covers changed shape, even though its code did not ─


def test_lingbot_fingerprint_is_not_only_the_weights() -> None:
    """🔴 The audit result, made executable.

    In every openpi repo the LFS set **is** the weights, so the fingerprint was
    de facto "these weights". In the LingBot reference repo nine files are LFS
    and only six of them are the VLA shards: three README images, a frozen depth
    model, a frozen video teacher and the tokenizer travel as LFS too. Deleting
    a picture therefore changes the fingerprint.

    That is not a bug in ``model_hash`` — it is the same rule applied to a
    differently shaped repo — but it means plagiarism dedup gets weaker exactly
    when the base model changes, and the backend has to know it.
    """
    full = model_hash_from_hf_tree(LINGBOT_REFERENCE_TREE)
    without_a_picture = model_hash_from_hf_tree(
        _without(LINGBOT_REFERENCE_TREE, "assets/lingbot_vla2_framework.png")
    )
    assert full != without_a_picture

    # The same edit on an openpi repo changes nothing, because its non-weight
    # files were never LFS.
    assert (
        model_hash_from_hf_tree(_without(UID221_PYTORCH_TREE, "config.json"))
        == UID221_MODEL_HASH
    )


def _lfs_oids(tree: list[dict[str, Any]]) -> dict[str, str]:
    return {e["path"]: e["lfs"]["oid"] for e in tree if e.get("lfs")}


def test_fine_tuning_leaves_no_shard_byte_identical() -> None:
    """🔴 The false-positive direction, measured on the vendor's own two repos.

    The fear this answers: LingBot ships 25.5 GB over six shards while a miner
    only moves some of the parameters, so two miners might end up sharing whole
    shards byte for byte and be marked as copies of each other. On the only real
    before/after pair that exists — the base model and the vendor's RoboTwin
    post-trained artifact — **not one of the six shards survives**: 0 of 6 oids
    match. The single LFS file the two repos share is ``tokenizer.json``, which
    is not a weight at all.

    Even that overlap cannot cause a false positive: the fingerprint is one
    sha256 over the **whole** sorted oid list, so it is all-or-nothing. Sharing
    11 of 12 files produces a completely unrelated fingerprint, not a near miss
    — there is no per-shard matching anywhere in the algorithm. A collision
    still means every LFS file is byte-identical.
    """
    base, post = _lfs_oids(LINGBOT_REFERENCE_TREE), _lfs_oids(LINGBOT_POST_TRAINED_TREE)
    shards = {p: o for p, o in post.items() if p.endswith(".safetensors")}
    assert len(shards) == 6
    assert not set(shards.values()) & set(base.values())

    shared = set(base.values()) & set(post.values())
    assert shared == {base["tokenizer.json"]}

    assert model_hash_from_hf_tree(
        LINGBOT_POST_TRAINED_TREE
    ) != model_hash_from_hf_tree(LINGBOT_REFERENCE_TREE)


def test_lingbot_shard_bytes_are_export_dependent() -> None:
    """🔴 The false-negative direction: the same tensors can land in different
    shard bytes, and then the fingerprint no longer recognises them.

    Both revisions' ``model.safetensors.index.json`` name **the same 1708
    tensors** and declare the same ``total_size`` (25 503 630 044 bytes), yet
    **1446 of those 1708 tensors sit in a different shard file** in the
    post-trained repo, and no two shards have the same length. Where a tensor
    lands is a property of whoever ran the export — not of the weights.

    So "same weights, re-exported" is not guaranteed to produce the same
    fingerprint, and pinning the six shard names does not fix it. Catching that
    would mean hashing tensors rather than files, which is a different algorithm
    and a different decision; this case only stops the risk from being forgotten.
    """
    sizes = {
        name: sorted(e["size"] for e in tree if e["path"].endswith(".safetensors"))
        for name, tree in (
            ("base", LINGBOT_REFERENCE_TREE),
            ("post", LINGBOT_POST_TRAINED_TREE),
        )
    }
    assert not set(sizes["base"]) & set(sizes["post"])
    assert abs(sum(sizes["base"]) - sum(sizes["post"])) < 1024


def test_lingbot_fingerprint_survives_a_repo_rename() -> None:
    """The property dedup actually depends on: same weights, different repo, same
    fingerprint. Paths are not part of the input, so this holds unchanged."""
    renamed = [{**e, "path": f"copy/{e['path']}"} for e in LINGBOT_REFERENCE_TREE]
    assert model_hash_from_hf_tree(renamed) == model_hash_from_hf_tree(
        LINGBOT_REFERENCE_TREE
    )


def test_golden_tree_count_is_pinned() -> None:
    """Five module-level trees: three real openpi submissions, the vendor's
    reference checkpoint, and the vendor's post-trained artifact. Adding a sixth
    has to be a deliberate edit here."""
    trees = [
        v for k, v in globals().items() if k.endswith("_TREE") and isinstance(v, list)
    ]
    assert len(trees) == 5


def test_lingbot_fixtures_declare_their_source() -> None:
    """A fixture with no provenance only proves the code matches whoever wrote
    it. Keep repo / revision / date next to the tree — both LingBot trees are
    now observed listings, and the pinned revision is what makes that checkable
    by anyone else."""
    source = Path(__file__).read_text(encoding="utf-8")
    header = source.split("LINGBOT_REFERENCE_TREE = _tree(")[0][-900:]
    for marker in ("repo=", "revision=", "fetched="):
        assert marker in header, marker

    post_header = source.split("LINGBOT_POST_TRAINED_TREE = _tree(")[0][-1600:]
    for marker in ("repo=", "revision=", "published=", "listed=", "Provenance"):
        assert marker in post_header, marker
