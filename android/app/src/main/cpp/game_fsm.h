#pragma once

#include <string>
#include <cstdint>

namespace mahjong {

enum GameState {
    STATE_IDLE = 0,       // 空闲 / 大厅
    STATE_SWAP = 1,       // 换三张
    STATE_DINGQUE = 2,    // 定缺选门
    STATE_PLAYING = 3,    // 正式对局摸打中
    STATE_SETTLED = 4     // 结算
};

class GameFSM {
public:
    GameFSM();

    void reset();

    // 更新状态机
    // raw_hand_count: 当前帧检测到的手牌张数 (0..14)
    // is_swap_detected: 视觉是否检出换三张特征
    // is_dq_detected: 视觉是否检出定缺盘特征
    // dingque_suit: 当前确认的定缺门 (0..2, -1为未定)
    // is_table_detected: 是否处于麻将牌桌画面
    // 返回: 当前游戏状态
    GameState update(
        int raw_hand_count,
        bool is_swap_detected,
        bool is_dq_detected,
        int dingque_suit,
        bool is_table_detected
    );

    GameState get_state() const { return current_state_; }
    std::string get_state_string() const;

    // 是否需要触发记牌器与牌局硬重置
    bool should_reset_memory() const { return need_memory_reset_; }
    void acknowledge_reset() { need_memory_reset_ = false; }

private:
    GameState current_state_;
    int empty_hand_frames_;
    int non_table_frames_;
    int state_confirm_frames_;
    bool need_memory_reset_;
};

} // namespace mahjong
