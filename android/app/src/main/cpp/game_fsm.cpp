#include "game_fsm.h"

namespace mahjong {

GameFSM::GameFSM() {
    reset();
}

void GameFSM::reset() {
    current_state_ = STATE_IDLE;
    empty_hand_frames_ = 0;
    non_table_frames_ = 0;
    state_confirm_frames_ = 0;
    need_memory_reset_ = true;
}

std::string GameFSM::get_state_string() const {
    switch (current_state_) {
        case STATE_IDLE: return "idle";
        case STATE_SWAP: return "swap";
        case STATE_DINGQUE: return "dingque";
        case STATE_PLAYING: return "playing";
        case STATE_SETTLED: return "settled";
    }
    return "unknown";
}

GameState GameFSM::update(
    int raw_hand_count,
    bool is_swap_detected,
    bool is_dq_detected,
    int dingque_suit,
    bool is_table_detected
) {
    if (!is_table_detected) {
        non_table_frames_++;
        // 连续 20 帧非牌桌画面判定离开对局
        if (non_table_frames_ >= 20 && current_state_ != STATE_IDLE) {
            current_state_ = STATE_IDLE;
            need_memory_reset_ = true;
        }
        return current_state_;
    }
    non_table_frames_ = 0;

    switch (current_state_) {
        case STATE_IDLE: {
            if (is_swap_detected) {
                state_confirm_frames_++;
                if (state_confirm_frames_ >= 2) {
                    current_state_ = STATE_SWAP;
                    need_memory_reset_ = true;
                    state_confirm_frames_ = 0;
                }
            } else if (is_dq_detected) {
                state_confirm_frames_++;
                if (state_confirm_frames_ >= 2) {
                    current_state_ = STATE_DINGQUE;
                    need_memory_reset_ = true;
                    state_confirm_frames_ = 0;
                }
            } else if (raw_hand_count >= 13) {
                current_state_ = STATE_PLAYING;
                need_memory_reset_ = true;
                state_confirm_frames_ = 0;
            } else {
                state_confirm_frames_ = 0;
            }
            break;
        }

        case STATE_SWAP: {
            if (is_dq_detected) {
                current_state_ = STATE_DINGQUE;
            } else if (raw_hand_count >= 13) {
                // 换完三张直接进入定缺或对局
                current_state_ = (dingque_suit != -1) ? STATE_PLAYING : STATE_DINGQUE;
            }
            break;
        }

        case STATE_DINGQUE: {
            // 定缺完成标志: 确立了定缺门，或手牌已有且无定缺大圆盘
            if (dingque_suit != -1 || (!is_dq_detected && raw_hand_count >= 13)) {
                current_state_ = STATE_PLAYING;
            }
            break;
        }

        case STATE_PLAYING: {
            // 【锁步铁律】对局中绝对不回退换三张或定缺阶段！彻底消除粘连。
            if (raw_hand_count == 0) {
                empty_hand_frames_++;
                // 连续 25 帧无牌确认为胡牌结算或新局重置
                if (empty_hand_frames_ >= 25) {
                    current_state_ = STATE_SETTLED;
                    need_memory_reset_ = true;
                    empty_hand_frames_ = 0;
                }
            } else {
                empty_hand_frames_ = 0;
            }
            break;
        }

        case STATE_SETTLED: {
            current_state_ = STATE_IDLE;
            break;
        }
    }

    return current_state_;
}

} // namespace mahjong
