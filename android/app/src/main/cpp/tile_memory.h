#pragma once

#include "sichuan_solver.h"
#include <array>
#include <string>
#include <vector>
#include <mutex>

namespace mahjong {

class TileMemory {
public:
    TileMemory();

    // 重置牌局记牌账本 (开局 / 换局 / 重新开始)
    void reset();

    // 瞬时手牌感知与出牌推断
    // new_hand_counts: 长度 34 数组
    // 返回: 刚打出的牌索引 (-1 表示无出牌事件或多张跳变)
    int update_hand(const std::array<int, NUM_TILES_TOTAL>& new_hand_counts);

    // 录入牌河弃牌 (视觉或外部来源)
    void add_discard(int tile_idx, int count = 1);

    // 录入碰杠副露 (视觉或外部来源)
    void add_meld(int tile_idx, int count = 3);

    // 获取当前场上真实存活剩余牌 (各牌 0..4)
    std::array<int, NUM_TILES_TOTAL> get_remaining_tiles() const;

    // 获取剩余牌总张数 (0..108)
    int get_total_remaining() const;

    // 获取当前手牌各牌张数
    std::array<int, NUM_TILES_TOTAL> get_hand_counts() const;

    // 获取已出牌统计
    std::array<int, NUM_TILES_TOTAL> get_discard_counts() const;

    // 导出 9x3 剩余牌矩阵 JSON 字符串 (供 UI 展示)
    std::string get_remaining_matrix_json() const;

private:
    mutable std::mutex mtx_;
    std::array<int, NUM_TILES_TOTAL> hand_counts_;
    std::array<int, NUM_TILES_TOTAL> visual_discards_;
    std::array<int, NUM_TILES_TOTAL> visual_melds_;
    std::array<int, NUM_TILES_TOTAL> inferred_discards_;
    int last_stable_hand_size_;
};

} // namespace mahjong
