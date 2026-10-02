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
// 27..33: 1z..7z (东南西北白发中，川麻不用，兼容预留)
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

struct DiscardAdvice {
    int tile_idx;                // 建议打出的牌 (0..26)
    std::string tile_mpsz;       // "1m", "5p", etc.
    std::string tile_chinese;    // "一万", "五筒", etc.
    int shanten_after;           // 打出后的向听数
    int ukeire_theoretical;      // 理论全进张数
    int ukeire_live;             // 真实场上存活进张数 (根据记牌器扣减)
    double ev_score;             // 综合期望估值 EV
    std::vector<int> waiting_tiles; // 进张牌列表
    std::string tag;             // "定缺推荐", "清一色诱导", "七对向", "最大进张", etc.
};

class SichuanSolver {
public:
    SichuanSolver() = default;

    // 计算当前手牌向听数 (普通和牌形 + 七对子取最小)
    // hand_counts: 长度 34 的数组，各牌张数 (0..4)
    // dingque_suit: 0:万, 1:筒, 2:条, -1:无定缺
    static int calculate_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit = -1);

    // 标准 4 面子 + 1 雀头向听数
    static int calculate_normal_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit = -1);

    // 七对子向听数
    static int calculate_chitoi_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit = -1);

    // 综合出牌建议与 EV 排序
    // hand_counts: 当前手牌各牌张数 (总和应为 14, 11, 8, 5, 2)
    // remaining_tiles: 牌池与对手未见剩余牌量 (长度 34, 各牌 0..4)
    // dingque_suit: 0:万, 1:筒, 2:条, -1:未定
    static std::vector<DiscardAdvice> evaluate_hand(
        const std::array<int, NUM_TILES_TOTAL>& hand_counts,
        const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
        int dingque_suit = -1
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
