#pragma once

#include <vector>
#include <string>
#include <array>
#include <algorithm>
#include <cstdint>

namespace mahjong {

// 0..8: 1m..9m
// 9..17: 1p..9p
// 18..26: 1s..9s
// 27..33: 1z..7z (东南西北白发中)
constexpr int NUM_TILES_SICHUAN = 27;
constexpr int NUM_TILES_TOTAL = 34;

enum Suit {
    SUIT_WAN = 0,   // 万 0..8
    SUIT_TONG = 1,  // 筒 9..17
    SUIT_TIAO = 2,  // 条 18..26
    SUIT_NONE = -1
};

inline Suit get_tile_suit(int tile_idx) {
    if (tile_idx >= 0 && tile_idx <= 8) return SUIT_WAN;
    if (tile_idx >= 9 && tile_idx <= 17) return SUIT_TONG;
    if (tile_idx >= 18 && tile_idx <= 26) return SUIT_TIAO;
    return SUIT_NONE;
}

inline int tile_number(int tile_idx) {
    return (tile_idx % 9) + 1;
}

std::string tile_to_mpsz(int tile_idx);
int mpsz_to_tile(const std::string& mpsz);
std::string tile_to_chinese(int tile_idx);

struct OpponentInfo {
    int seat = 1;                // 1:下家, 2:对家, 3:上家
    std::string name = "对手";   // "下家", etc.
    int dingque_suit = -1;       // 0:万, 1:筒, 2:条, -1:未定
    std::vector<int> discards;   // 弃牌
    std::vector<int> melds;      // 副露
    int standing_count = 13;     // 存活立牌数
    double tenpai_prob = 0.20;   // 听牌概率
};

struct HeldTileProb {
    int tile_idx = 0;
    std::string tile_mpsz;
    std::string tile_chinese;
    double prob = 0.0;
};

struct BayesianHandRange {
    int seat = 1;
    std::string name;
    int dingque = -1;
    std::string dingque_name = "未定";
    int standing = 13;
    double tenpai_prob = 0.20;
    std::vector<HeldTileProb> top_held;
};

struct DangerFlow {
    int tile_idx = 0;
    std::string tile_mpsz;
    double deal_in_prob = 0.0;
    std::string danger_level = "safe";   // "safe", "low", "medium", "high", "critical"
    std::string danger_reason = "安全";
    int max_threat_seat = -1;
};

struct WinEquityGaugeResult {
    double win_equity = 0.50;    // 0.0 .. 1.0
    int win_rate = 50;           // 0 .. 100 %
    double net_ev = 0.0;
    std::string level = "neutral"; // "extreme", "high", "neutral", "risk"
    std::string badge;
    std::string insight;
};

struct DiscardAdvice {
    int tile_idx = 0;            // 建议打出的牌 (0..26)
    std::string tile_mpsz;       // "1m", "5p", etc.
    std::string tile_chinese;    // "一万", "五筒", etc.
    int shanten_after = 0;       // 打出后的向听数
    int ukeire_theoretical = 0;  // 理论全进张数
    int ukeire_live = 0;         // 真实场上存活进张数 (根据记牌器扣减)
    double ev_score = 0.0;       // 综合期望估值 EV
    std::vector<int> waiting_tiles; // 进张牌列表
    std::string tag;             // "定缺推荐", "清一色诱导", "七对向", "最大进张", etc.
    bool is_dingque = false;     // 是否为定缺门必打牌
    double win_equity = 0.0;     // 实时胡牌胜率
    double policy_prob = 0.0;    // 强化学习策略网络先验概率
    DangerFlow danger_flow;      // 危险流向反推
    WinEquityGaugeResult ev_gauge; // 收益雷达
};

class SichuanSolver {
public:
    SichuanSolver() = default;

    // 计算当前手牌向听数 (普通和牌形 + 七对子取最小)
    static int calculate_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit = -1);

    // 标准 4 面子 + 1 雀头向听数
    static int calculate_normal_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit = -1);

    // 七对子向听数
    static int calculate_chitoi_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit = -1);

    // 综合出牌建议与 EV 排序 (兼容旧接口)
    static std::vector<DiscardAdvice> evaluate_hand(
        const std::array<int, NUM_TILES_TOTAL>& hand_counts,
        const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
        int dingque_suit = -1
    );

    // 综合出牌建议与 EV 排序 (全功能：含贝叶斯透视 + 胜率雷达 + PVN)
    static std::vector<DiscardAdvice> evaluate_hand_full(
        const std::array<int, NUM_TILES_TOTAL>& hand_counts,
        const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
        int dingque_suit = -1,
        const std::vector<OpponentInfo>& opponents = {}
    );

    // 贝叶斯手牌透视
    static std::vector<BayesianHandRange> calculate_bayesian_hand_ranges(
        const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
        const std::vector<OpponentInfo>& opponents
    );

    // 危险流向反推
    static DangerFlow calculate_danger_flow(
        int tile_idx,
        const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
        const std::vector<OpponentInfo>& opponents
    );

    // 胜率与期望收益雷达计算
    static WinEquityGaugeResult calculate_win_equity_and_gauge(
        int shanten,
        int ukeire_live,
        int total_remaining,
        const std::vector<OpponentInfo>& opponents,
        double max_deal_in_prob,
        int expected_fan = 1
    );

    // 强化学习策略价值网络推理
    static void evaluate_policy_value(
        const std::array<int, NUM_TILES_TOTAL>& hand_counts,
        const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
        std::array<double, NUM_TILES_TOTAL>& out_policy,
        double& out_value
    );

private:
    static void search_normal(
        std::array<int, NUM_TILES_TOTAL>& counts,
        int tile_idx,
        int mentsu,
        int taatsu,
        int jantai,
        int& min_shanten
    );
};

} // namespace mahjong
