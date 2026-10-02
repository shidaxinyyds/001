#include "tile_memory.h"
#include <sstream>
#include <algorithm>

namespace mahjong {

TileMemory::TileMemory() {
    reset();
}

void TileMemory::reset() {
    std::lock_guard<std::mutex> lock(mtx_);
    hand_counts_.fill(0);
    visual_discards_.fill(0);
    visual_melds_.fill(0);
    inferred_discards_.fill(0);
    last_stable_hand_size_ = 0;
    opponent_dingques_.fill(-1);
    for (int i = 0; i < 4; ++i) {
        opponent_discards_[i].clear();
        opponent_melds_[i].clear();
    }
}

int TileMemory::update_hand(const std::array<int, NUM_TILES_TOTAL>& new_hand_counts) {
    std::lock_guard<std::mutex> lock(mtx_);

    int new_size = 0;
    for (int i = 0; i < NUM_TILES_TOTAL; ++i) {
        new_size += new_hand_counts[i];
    }

    int discarded_tile = -1;

    // 瞬时出牌判定:
    // 上一次稳定手牌为出牌态 (14, 11, 8, 5, 2)，当前手牌张数刚好少 1 张 (13, 10, 7, 4, 1)
    if ((last_stable_hand_size_ == 14 && new_size == 13) ||
        (last_stable_hand_size_ == 11 && new_size == 10) ||
        (last_stable_hand_size_ == 8 && new_size == 7) ||
        (last_stable_hand_size_ == 5 && new_size == 4) ||
        (last_stable_hand_size_ == 2 && new_size == 1)) {

        // 集合差分找出减少的那张牌
        for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
            if (hand_counts_[i] > new_hand_counts[i]) {
                discarded_tile = i;
                inferred_discards_[i] = std::min(4, inferred_discards_[i] + 1);
                opponent_discards_[0].push_back(i); // 我方打牌
                break; // 只计一张
            }
        }
    }

    hand_counts_ = new_hand_counts;
    if (new_size > 0) {
        last_stable_hand_size_ = new_size;
    }

    return discarded_tile;
}

void TileMemory::add_discard(int tile_idx, int count) {
    if (tile_idx < 0 || tile_idx >= NUM_TILES_TOTAL) return;
    std::lock_guard<std::mutex> lock(mtx_);
    visual_discards_[tile_idx] = std::min(4, visual_discards_[tile_idx] + count);
    // 视觉账本与推断账本单调融合，防止双重扣减
    if (visual_discards_[tile_idx] >= inferred_discards_[tile_idx]) {
        inferred_discards_[tile_idx] = 0;
    }
}

void TileMemory::add_meld(int tile_idx, int count) {
    if (tile_idx < 0 || tile_idx >= NUM_TILES_TOTAL) return;
    std::lock_guard<std::mutex> lock(mtx_);
    visual_melds_[tile_idx] = std::min(4, visual_melds_[tile_idx] + count);
}

void TileMemory::set_opponent_dingque(int seat, int suit) {
    if (seat < 0 || seat >= 4) return;
    std::lock_guard<std::mutex> lock(mtx_);
    opponent_dingques_[seat] = suit;
}

int TileMemory::get_opponent_dingque(int seat) const {
    if (seat < 0 || seat >= 4) return -1;
    std::lock_guard<std::mutex> lock(mtx_);
    return opponent_dingques_[seat];
}

void TileMemory::add_opponent_discard(int seat, int tile_idx) {
    if (seat < 0 || seat >= 4 || tile_idx < 0 || tile_idx >= NUM_TILES_TOTAL) return;
    std::lock_guard<std::mutex> lock(mtx_);
    opponent_discards_[seat].push_back(tile_idx);
    visual_discards_[tile_idx] = std::min(4, visual_discards_[tile_idx] + 1);
    if (visual_discards_[tile_idx] >= inferred_discards_[tile_idx]) {
        inferred_discards_[tile_idx] = 0;
    }
}

std::vector<int> TileMemory::get_opponent_discards(int seat) const {
    if (seat < 0 || seat >= 4) return {};
    std::lock_guard<std::mutex> lock(mtx_);
    return opponent_discards_[seat];
}

void TileMemory::add_opponent_meld(int seat, int tile_idx, int count) {
    if (seat < 0 || seat >= 4 || tile_idx < 0 || tile_idx >= NUM_TILES_TOTAL) return;
    std::lock_guard<std::mutex> lock(mtx_);
    for (int i = 0; i < count; ++i) {
        opponent_melds_[seat].push_back(tile_idx);
    }
    visual_melds_[tile_idx] = std::min(4, visual_melds_[tile_idx] + count);
}

std::vector<int> TileMemory::get_opponent_melds(int seat) const {
    if (seat < 0 || seat >= 4) return {};
    std::lock_guard<std::mutex> lock(mtx_);
    return opponent_melds_[seat];
}

std::array<int, NUM_TILES_TOTAL> TileMemory::get_remaining_tiles() const {
    std::lock_guard<std::mutex> lock(mtx_);
    std::array<int, NUM_TILES_TOTAL> remaining;
    for (int i = 0; i < NUM_TILES_TOTAL; ++i) {
        int disc = std::max(visual_discards_[i], inferred_discards_[i]);
        int visible = hand_counts_[i] + disc + visual_melds_[i];
        remaining[i] = std::max(0, 4 - visible);
    }
    return remaining;
}

int TileMemory::get_total_remaining() const {
    auto rem = get_remaining_tiles();
    int sum = 0;
    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        sum += rem[i];
    }
    return sum;
}

std::array<int, NUM_TILES_TOTAL> TileMemory::get_hand_counts() const {
    std::lock_guard<std::mutex> lock(mtx_);
    return hand_counts_;
}

std::array<int, NUM_TILES_TOTAL> TileMemory::get_discard_counts() const {
    std::lock_guard<std::mutex> lock(mtx_);
    std::array<int, NUM_TILES_TOTAL> disc;
    for (int i = 0; i < NUM_TILES_TOTAL; ++i) {
        disc[i] = std::max(visual_discards_[i], inferred_discards_[i]);
    }
    return disc;
}

std::string TileMemory::get_remaining_matrix_json() const {
    auto rem = get_remaining_tiles();
    std::ostringstream ss;
    ss << "{\"m\":[";
    for (int i = 0; i < 9; ++i) {
        if (i > 0) ss << ",";
        ss << rem[i];
    }
    ss << "],\"p\":[";
    for (int i = 9; i < 18; ++i) {
        if (i > 9) ss << ",";
        ss << rem[i];
    }
    ss << "],\"s\":[";
    for (int i = 18; i < 27; ++i) {
        if (i > 18) ss << ",";
        ss << rem[i];
    }
    ss << "]}";
    return ss.str();
}

} // namespace mahjong
