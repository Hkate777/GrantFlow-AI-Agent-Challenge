"""語義注入分類器的留出集評估骨架（真實標註資料仍待機構提供）。

做什麼
------
把種子語料按固定種子切分為訓練集與留出集：訓練集建 TF-IDF 模型，留出集
算精確率、召回率、誤報率與混淆矩陣。門檻可調，因此能看到「調門檻」在
召回與誤報之間的取捨。

能力邊界（不可省略）
--------------------
本評估用的是 ``semantic_guard`` 內建的**演示級種子語料**，不是真實標註資料。
得出的數字只描述「分類器在本 Demonstration 語料上可重現的表現」，**不得當作
真實場景的檢出率或誤報率引用**。正式機構須收集真實注入／正常樣本、留出標註
集重跑，並做偏差評估後，才對外宣稱偵測能力。報告的 ``caveat`` 欄位會隨結果
呈現這句聲明。
"""
from __future__ import annotations

import random
from typing import Iterable

from .semantic_guard import (
    CAPABILITY_STATEMENT,
    _BENIGN_CORPUS,
    _INJECTION_CORPUS,
    _TfidfModel,
    score_with_model,
)

CAVEAT = (
    "本數字基於演示級種子語料的留出集，不是真實標註資料的性能；"
    "不得當作真實檢出率／誤報率引用。" + CAPABILITY_STATEMENT
)


def holdout_eval(*, injection: Iterable[str] = _INJECTION_CORPUS,
                 benign: Iterable[str] = _BENIGN_CORPUS,
                 test_ratio: float = 0.3, seed: int = 7,
                 threshold: float = 0.55) -> dict:
    """以固定種子切分留出集，回傳混淆矩陣與精確率／召回率／誤報率。

    ``injection`` 視為正例（應判 suspicious），``benign`` 視為負例。
    語料太少或切分後某集為空時回 ``insufficient``，不臆造數字。
    """
    inj = list(injection)
    ben = list(benign)
    if len(inj) < 4 or len(ben) < 4:
        return {"status": "insufficient", "caveat": CAVEAT,
                "note": "正負例各至少 4 筆才能切分訓練／留出集。"}

    rng = random.Random(seed)
    shuffled_inj, shuffled_ben = inj[:], ben[:]
    rng.shuffle(shuffled_inj)
    rng.shuffle(shuffled_ben)

    def split(items: list[str]) -> tuple[list[str], list[str]]:
        count = max(1, int(round(len(items) * test_ratio)))
        count = min(count, len(items) - 1)
        return items[count:], items[:count]

    inj_train, inj_test = split(shuffled_inj)
    ben_train, ben_test = split(shuffled_ben)
    model = _TfidfModel(inj_train, ben_train)

    tp = sum(score_with_model(t, model, threshold=threshold).label == "suspicious"
             for t in inj_test)
    fp = sum(score_with_model(t, model, threshold=threshold).label == "suspicious"
             for t in ben_test)
    fn = len(inj_test) - tp
    tn = len(ben_test) - fp

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) else 0.0

    return {
        "status": "ok",
        "corpus": "演示級種子語料（非真實標註資料）",
        "parameters": {"seed": seed, "test_ratio": test_ratio, "threshold": threshold},
        "split": {"train": {"injection": len(inj_train), "benign": len(ben_train)},
                  "holdout": {"injection": len(inj_test), "benign": len(ben_test)}},
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "accuracy": round(accuracy, 4),
        "caveat": CAVEAT,
    }


def threshold_sweep(**kwargs) -> list[dict]:
    """在多個門檻上重跑留出集，直觀看到召回／誤報的取捨。"""
    results = []
    for level in (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9):
        report = holdout_eval(threshold=level, **kwargs)
        results.append({"threshold": level,
                        "recall": report.get("recall"),
                        "false_positive_rate": report.get("false_positive_rate")})
    return results
