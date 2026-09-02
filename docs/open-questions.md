# Open questions

Technical questions that are still open. One entry per question, each naming what
would settle it. An answered question leaves this file and becomes code, a test, or
a golden vector — not a paragraph saying it used to be uncertain.

## 1. Does the HuggingFace tree API still report `lfs.oid` for repositories created today?

`model_hash.py` fingerprints a checkpoint from the `sha256` of every LFS file in the
tree response (`lfs.sha256 or lfs.oid`), and an empty set returns the empty-string
sentinel, which the backend turns into `model_hash_empty` — a rejection.

Half of this is settled. The two LingBot-VLA 2.0 repositories measured on
2026-08-25 are already on Xet storage (their tree responses carry `xetHash`) and
they **still** report `lfs.oid`; one file was downloaded and hashed locally to
confirm that the oid really is the content sha256.

The unsettled half is a repository created by a miner in 2026, whose large files
were uploaded through the `xet` direct path. There is no sample of that response
shape. If such a response one day carries only `xetHash` and no `lfs` object, the
fingerprint degrades to the empty string and a miner who has already paid the entry
fee is **wrongly rejected**.

**What would settle it**: look at the raw tree response the first time a
miner-created LingBot repository lands on chain.

## 2. Is shard splitting stable across two runs of the same export pipeline?

Two revisions of the same 25.5 GB model hold the identical 1708 tensors and the
identical `metadata.total_size`, yet 1446 of those tensors landed in a different
shard file, so all six shard oids differ and so does the fingerprint. Which shard a
tensor lands in is a property of the export run, not of the weights.

Those two revisions were probably produced by two different pipelines, so they do
not answer whether one pipeline is reproducible. Note the answer only bounds the
risk, it does not remove it: miners run different world sizes and library versions
from each other regardless.

**What would settle it**: convert one dcp checkpoint to HF format twice with the
same script and world size, and compare the six oids.

## 3. What other non-weight LFS files will appear in miner exports?

The fingerprint covers every LFS file, not only weights, so a plagiarist who copies
someone's shards and swaps one README image gets a different fingerprint. The
official post-trained repository keeps only a `.png` and `tokenizer.json`; whether
miners will add wandb artifacts, videos or eval results is unknown, and there is no
real miner submission to look at yet. The direction is known, the magnitude is not.

**What would settle it**: the first real miner submissions on the LingBot base.
