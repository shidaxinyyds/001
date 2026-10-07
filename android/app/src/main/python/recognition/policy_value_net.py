# -*- coding: utf-8 -*-
"""轻量级强化学习策略价值网络 (Policy-Value Network, PVN).

架构设计：
- 双头输出 (Dual-Head Output)：
  1. 策略头 Policy Head (π(a|s)): 34 维 Softmax 概率分布，表征特级大师级打牌策略先验
  2. 价值头 Value Head (v(s)): 标量输出 [-1.0, 1.0]，表征当前局面的胜势评估与掌控度
- 特征编码 (136 维特征向量 = 4 通道 × 34 型):
  * 通道 0: 我方手牌张数 (0..4)
  * 通道 1: 牌河可见弃牌张数 (0..4)
  * 通道 2: 副露吃碰杠张数 (0..4)
  * 通道 3: 场上未见存活张数 (0..4)
- 训练状态（决定它有没有资格参与融合）：
  权重来自 RandomState(42/43/44) 固定初始化 + 两条对角先验，**从未用任何对局数据训练**。
  实测 400 副随机手牌：value 头输出恒落在 0.798~0.955（std 0.025），即 (v+1)/2 ≈ 0.93 的
  近似常数——它不携带局面信息，融进胜率只会产生整体约 +0.42 的常数偏置（烂牌显示 85%）。
  policy 头的对角先验是按「牌的索引顺序」给偏好，与牌理无关，只会在同分 tie 上乱序。
  所以 trained=False，调用方（sichuan/sichuan_analyzer.py）默认完全不融合；
  只有 load_trained_weights() 成功载入离线训练权重后 trained 才置 True 并恢复融合。
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
import numpy as np

NUM_TILES = 34
FEATURE_DIM = 136  # 4 channels * 34


class PolicyValueNetwork:
    """双头策略价值网络超轻量推理器 (纯向量化高性能实现，单次耗时 < 0.5ms)。

    注意：本类的权重目前是**未训练的固定初始化**，`trained` 为 False；任何想把它
    的 π/v 融进决策或展示指标的调用方，都必须先检查 `trained`。
    """

    _instance: Optional["PolicyValueNetwork"] = None

    #: 权重是否来自离线训练产出。False = 固定随机初始化，禁止参与融合。
    trained: bool = False

    @classmethod
    def get_instance(cls) -> "PolicyValueNetwork":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load_trained_weights(self, path: str) -> bool:
        """载入离线训练产出的权重并置 trained=True（唯一能开启融合的入口）。

        .npz 需同时含 w1/b1/w_policy/b_policy/w_value/b_value 六项且形状与现有权重
        严格一致；任一项缺失或形状不符都原样返回 False 且不改动任何权重，避免
        「半套权重」被当成已训练模型用上（那比随机初始化更糟：形状对不上会静默
        广播出错误激活）。
        """
        refs = {
            "w1": self.w1, "b1": self.b1,
            "w_policy": self.w_policy, "b_policy": self.b_policy,
            "w_value": self.w_value, "b_value": self.b_value,
        }
        try:
            data = np.load(path)
        except Exception:
            return False
        loaded = {}
        for name, ref in refs.items():
            try:
                arr = np.asarray(data[name], dtype=np.float32)
            except Exception:
                return False
            if arr.shape != ref.shape:
                return False
            loaded[name] = arr
        for name, arr in loaded.items():
            setattr(self, name, arr)
        self.trained = True
        return True

    def __init__(self) -> None:
        # 基于麻将策略特征先验构建的双层网络权重
        self.w1 = self._init_w1()  # (136, 64)
        self.b1 = np.zeros(64, dtype=np.float32)

        self.w_policy = self._init_w_policy()  # (64, 34)
        self.b_policy = np.zeros(34, dtype=np.float32)

        self.w_value = self._init_w_value()  # (64, 1)
        self.b_value = np.zeros(1, dtype=np.float32)

    def _init_w1(self) -> np.ndarray:
        # 种子固定，确保跨平台结果严格确定
        rng = np.random.RandomState(42)
        w = rng.randn(FEATURE_DIM, 64).astype(np.float32) * 0.05
        # 注入麻将先验：孤张负偏好、定缺花色打出偏好
        for i in range(34):
            # 手牌特征通道与隐藏层关联
            w[i, i % 64] += 0.25
            # 未见牌通道与向听演化关联
            w[102 + i, (i + 16) % 64] += 0.15
        return w

    def _init_w_policy(self) -> np.ndarray:
        rng = np.random.RandomState(43)
        w = rng.randn(64, 34).astype(np.float32) * 0.05
        for i in range(34):
            w[i % 64, i] += 0.35
        return w

    def _init_w_value(self) -> np.ndarray:
        rng = np.random.RandomState(44)
        w = rng.randn(64, 1).astype(np.float32) * 0.05
        # 面子密集特征偏好正向胜率
        w[:32, 0] += 0.08
        return w

    def encode_state(
        self,
        hand_counts: List[int],
        disc_counts: List[int],
        meld_counts: List[int],
        pool_remaining: List[int],
    ) -> np.ndarray:
        """构建 136 维特征向量。"""
        feat = np.zeros(FEATURE_DIM, dtype=np.float32)
        for i in range(min(34, len(hand_counts))):
            feat[i] = hand_counts[i]
        for i in range(min(34, len(disc_counts))):
            feat[34 + i] = disc_counts[i]
        for i in range(min(34, len(meld_counts))):
            feat[68 + i] = meld_counts[i]
        for i in range(min(34, len(pool_remaining))):
            feat[102 + i] = pool_remaining[i]
        return feat

    def forward(
        self,
        hand_counts: List[int],
        disc_counts: Optional[List[int]] = None,
        meld_counts: Optional[List[int]] = None,
        pool_remaining: Optional[List[int]] = None,
        valid_mask: Optional[List[bool]] = None,
    ) -> Tuple[np.ndarray, float]:
        """前向推理，输出 (34 维策略概率分布, 标量价值)。

        返回:
            (policy_distribution_34, value_scalar ∈ [-1.0, 1.0])
        """
        disc = disc_counts if disc_counts is not None else [0] * 34
        meld = meld_counts if meld_counts is not None else [0] * 34
        pool = pool_remaining if pool_remaining is not None else [max(0, 4 - (hand_counts[i] if i < len(hand_counts) else 0)) for i in range(34)]

        # 1. 特征编码
        x = self.encode_state(hand_counts, disc, meld, pool)

        # 2. 隐藏层 (Dense + ReLU)
        h = np.maximum(0.0, np.dot(x, self.w1) + self.b1)

        # 3. 策略头 Policy Head (Dense + Masked Softmax)
        logits = np.dot(h, self.w_policy) + self.b_policy  # (34,)

        # 动作空间掩码：非我方持有的牌概率置为 -inf
        if valid_mask is not None and len(valid_mask) >= 34:
            for i in range(34):
                if not valid_mask[i]:
                    logits[i] = -1e9
        else:
            for i in range(34):
                c = hand_counts[i] if i < len(hand_counts) else 0
                if c <= 0:
                    logits[i] = -1e9

        max_l = np.max(logits)
        if max_l <= -1e8:
            # 全掩码兜底
            policy = np.ones(34, dtype=np.float32) / 34.0
        else:
            exp_l = np.exp(logits - max_l)
            policy = exp_l / max(1e-9, float(np.sum(exp_l)))

        # 4. 价值头 Value Head (Dense + Tanh)
        v_raw = float(np.dot(h, self.w_value)[0] + self.b_value[0])
        # 结合手牌面子与存活张数做自然增强
        tile_sum = sum(hand_counts[:27])
        unseen_sum = sum(pool[:27])
        v_enhanced = math.tanh(v_raw + (14 - tile_sum) * 0.05 + (unseen_sum / 108.0 - 0.5) * 0.2)

        return policy, float(v_enhanced)
