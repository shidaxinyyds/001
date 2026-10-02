#include "sichuan_solver.h"
#include <numeric>
#include <cmath>
#include <sstream>

namespace mahjong {

static const char* CHINESE_NAMES[NUM_TILES_TOTAL] = {
    "一万", "二万", "三万", "四万", "五万", "六万", "七万", "八万", "九万",
    "一筒", "二筒", "三筒", "四筒", "五筒", "六筒", "七筒", "八筒", "九筒",
    "一条", "二条", "三条", "四条", "五条", "六条", "七条", "八条", "九条",
    "东风", "南风", "西风", "北风", "红中", "发财", "白板"
};

std::string tile_to_chinese(int tile_idx) {
    if (tile_idx >= 0 && tile_idx < NUM_TILES_TOTAL) {
        return CHINESE_NAMES[tile_idx];
    }
    return "未知";
}

std::string tile_to_mpsz(int tile_idx) {
    if (tile_idx >= 0 && tile_idx <= 8) {
        return std::to_string(tile_idx + 1) + "m";
    }
    if (tile_idx >= 9 && tile_idx <= 17) {
        return std::to_string(tile_idx - 9 + 1) + "p";
    }
    if (tile_idx >= 18 && tile_idx <= 26) {
        return std::to_string(tile_idx - 18 + 1) + "s";
    }
    if (tile_idx >= 27 && tile_idx <= 33) {
        return std::to_string(tile_idx - 27 + 1) + "z";
    }
    return "";
}

int mpsz_to_tile(const std::string& mpsz) {
    if (mpsz.length() < 2) return -1;
    int num = mpsz[0] - '0';
    char suit = mpsz[1];
    if (num < 1 || num > 9) return -1;
    if (suit == 'm') return num - 1;
    if (suit == 'p') return 9 + (num - 1);
    if (suit == 's') return 18 + (num - 1);
    if (suit == 'z' && num <= 7) return 27 + (num - 1);
    return -1;
}

int SichuanSolver::calculate_chitoi_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit) {
    int pairs = 0;
    int distinct_tiles = 0;

    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        if (dingque_suit != -1 && get_tile_suit(i) == dingque_suit) {
            continue;
        }
        if (hand_counts[i] >= 2) {
            pairs++;
        }
        if (hand_counts[i] >= 1) {
            distinct_tiles++;
        }
    }

    int shanten = 6 - pairs;
    if (distinct_tiles < 7) {
        shanten += (7 - distinct_tiles);
    }
    return shanten;
}

void SichuanSolver::search_normal(
    std::array<int, NUM_TILES_TOTAL>& counts,
    int tile_idx,
    int mentsu,
    int taatsu,
    int jantai,
    int& min_shanten
) {
    // 剪枝
    int current_shanten = 8 - 2 * mentsu - taatsu - jantai;
    if (current_shanten < min_shanten) {
        min_shanten = current_shanten;
    }

    // 找到下一个有牌的位置
    while (tile_idx < NUM_TILES_SICHUAN && counts[tile_idx] == 0) {
        tile_idx++;
    }

    if (tile_idx >= NUM_TILES_SICHUAN) {
        return;
    }

    int t = tile_idx;

    // 1. 尝试作为雀头 (对子)
    if (jantai == 0 && counts[t] >= 2) {
        counts[t] -= 2;
        search_normal(counts, t, mentsu, taatsu, 1, min_shanten);
        counts[t] += 2;
    }

    // 2. 尝试作为刻子 (3张相同)
    if (counts[t] >= 3) {
        counts[t] -= 3;
        search_normal(counts, t, mentsu + 1, taatsu, jantai, min_shanten);
        counts[t] += 3;
    }

    // 3. 尝试作为顺子 (t, t+1, t+2)
    // 必须在同一花色内 (t%9 <= 6)
    if ((t % 9) <= 6 && counts[t + 1] > 0 && counts[t + 2] > 0) {
        counts[t]--;
        counts[t + 1]--;
        counts[t + 2]--;
        search_normal(counts, t, mentsu + 1, taatsu, jantai, min_shanten);
        counts[t]++;
        counts[t + 1]++;
        counts[t + 2]++;
    }

    // 4. 尝试作为搭子 (两面/边张: t, t+1)
    if ((t % 9) <= 7 && counts[t + 1] > 0 && (mentsu + taatsu < 4)) {
        counts[t]--;
        counts[t + 1]--;
        search_normal(counts, t, mentsu, taatsu + 1, jantai, min_shanten);
        counts[t]++;
        counts[t + 1]++;
    }

    // 5. 尝试作为嵌张搭子 (t, t+2)
    if ((t % 9) <= 6 && counts[t + 2] > 0 && (mentsu + taatsu < 4)) {
        counts[t]--;
        counts[t + 2]--;
        search_normal(counts, t, mentsu, taatsu + 1, jantai, min_shanten);
        counts[t]++;
        counts[t + 2]++;
    }

    // 6. 尝试作为单张搭子 (对子当作搭子看)
    if (counts[t] >= 2 && (mentsu + taatsu < 4)) {
        counts[t] -= 2;
        search_normal(counts, t, mentsu, taatsu + 1, jantai, min_shanten);
        counts[t] += 2;
    }

    // 7. 跳过此牌作为孤张处理
    int temp = counts[t];
    counts[t] = 0;
    search_normal(counts, t + 1, mentsu, taatsu, jantai, min_shanten);
    counts[t] = temp;
}

int SichuanSolver::calculate_normal_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit) {
    std::array<int, NUM_TILES_TOTAL> counts = hand_counts;

    // 清除缺门牌
    if (dingque_suit != -1) {
        int start = dingque_suit * 9;
        for (int i = 0; i < 9; ++i) {
            counts[start + i] = 0;
        }
    }

    int min_shanten = 8;
    search_normal(counts, 0, 0, 0, 0, min_shanten);
    return min_shanten;
}

int SichuanSolver::calculate_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit) {
    int normal_shanten = calculate_normal_shanten(hand_counts, dingque_suit);
    int chitoi_shanten = calculate_chitoi_shanten(hand_counts, dingque_suit);
    return std::min(normal_shanten, chitoi_shanten);
}

std::vector<DiscardAdvice> SichuanSolver::evaluate_hand(
    const std::array<int, NUM_TILES_TOTAL>& hand_counts,
    const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
    int dingque_suit
) {
    std::vector<DiscardAdvice> advices;

    // 1. 统计当前手牌花色张数分布与是否有缺门牌
    std::array<int, 3> suit_counts = {0, 0, 0};
    std::vector<int> dingque_tiles;

    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        if (hand_counts[i] > 0) {
            Suit s = get_tile_suit(i);
            if (s != SUIT_NONE) {
                suit_counts[s] += hand_counts[i];
            }
            if (dingque_suit != -1 && s == dingque_suit) {
                dingque_tiles.push_back(i);
            }
        }
    }

    // 2. 定缺天条门控 (DingQue Policy):
    // 若手牌含有定缺门牌，强制推荐打出定缺门牌，绝对屏蔽保留门牌
    if (!dingque_tiles.empty()) {
        for (int t : dingque_tiles) {
            DiscardAdvice adv;
            adv.tile_idx = t;
            adv.tile_mpsz = tile_to_mpsz(t);
            adv.tile_chinese = tile_to_chinese(t);
            adv.shanten_after = calculate_shanten(hand_counts, dingque_suit);
            adv.ukeire_theoretical = 0;
            adv.ukeire_live = 0;
            adv.tag = "定缺必打";
            adv.is_dingque = true;

            // 优先级打分：
            // 孤张 1, 9 (100) > 孤张 2, 8 (90) > 孤张 3..7 (80) > 搭子/对子
            int num = tile_number(t);
            int base_priority = 50;
            if (hand_counts[t] == 1) {
                // 判断是否孤张
                bool has_neighbor = false;
                if ((t % 9) > 0 && hand_counts[t - 1] > 0) has_neighbor = true;
                if ((t % 9) < 8 && hand_counts[t + 1] > 0) has_neighbor = true;
                if ((t % 9) > 1 && hand_counts[t - 2] > 0) has_neighbor = true;
                if ((t % 9) < 7 && hand_counts[t + 2] > 0) has_neighbor = true;

                if (!has_neighbor) {
                    if (num == 1 || num == 9) base_priority = 100;
                    else if (num == 2 || num == 8) base_priority = 90;
                    else base_priority = 80;
                } else {
                    base_priority = 65;
                }
            } else if (hand_counts[t] == 2) {
                base_priority = 40; // 对子稍后打
            } else {
                base_priority = 20; // 刻子最后打
            }

            adv.ev_score = base_priority;
            advices.push_back(adv);
        }

        // 按定缺打牌优先级排序
        std::sort(advices.begin(), advices.end(), [](const DiscardAdvice& a, const DiscardAdvice& b) {
            return a.ev_score > b.ev_score;
        });
        return advices;
    }

    // 3. 无缺门牌时的标准牌效与 EV 评估
    // 判断清一色倾向 (某花色 >= 8 张)
    int flush_suit = -1;
    for (int s = 0; s < 3; ++s) {
        if (s != dingque_suit && suit_counts[s] >= 8) {
            flush_suit = s;
            break;
        }
    }

    // 遍历每一个候选舍牌
    for (int d = 0; d < NUM_TILES_SICHUAN; ++d) {
        if (hand_counts[d] <= 0) continue;

        // 临时打出 d
        std::array<int, NUM_TILES_TOTAL> h_after = hand_counts;
        h_after[d]--;

        int base_shanten = calculate_shanten(h_after, dingque_suit);

        int ukeire_theo = 0;
        int ukeire_live = 0;
        std::vector<int> waiting;

        // 尝试摸入每一张合法牌 t (非缺门)
        for (int t = 0; t < NUM_TILES_SICHUAN; ++t) {
            if (dingque_suit != -1 && get_tile_suit(t) == dingque_suit) continue;

            h_after[t]++;
            int new_shanten = calculate_shanten(h_after, dingque_suit);
            h_after[t]--;

            if (new_shanten < base_shanten) {
                // t 为有效进张
                waiting.push_back(t);
                int rem_theo = 4 - hand_counts[t];
                int rem_live = remaining_tiles[t];
                ukeire_theo += std::max(0, rem_theo);
                ukeire_live += std::max(0, rem_live);
            }
        }

        DiscardAdvice adv;
        adv.tile_idx = d;
        adv.tile_mpsz = tile_to_mpsz(d);
        adv.tile_chinese = tile_to_chinese(d);
        adv.shanten_after = base_shanten;
        adv.ukeire_theoretical = ukeire_theo;
        adv.ukeire_live = ukeire_live;
        adv.waiting_tiles = waiting;

        // EV 打分公式:
        // EV = (8 - shanten) * 100 + ukeire_live * 8 + fan_potential
        double ev = (8 - base_shanten) * 100.0 + ukeire_live * 8.0;

        // 清一色诱导因子
        if (flush_suit != -1) {
            Suit d_suit = get_tile_suit(d);
            if (d_suit != flush_suit) {
                ev += 250.0; // 坚决打出杂色
                adv.tag = "清一色逼退杂色";
            } else {
                ev -= 150.0; // 尽量不破坏主花色
                adv.tag = "保留清一色";
            }
        } else if (base_shanten == 0) {
            adv.tag = "叫听推荐";
        } else if (ukeire_live >= 12) {
            adv.tag = "极佳进张";
        } else {
            adv.tag = "最大存活牌效";
        }

        // 带根 / 刮风下雨诱导: 手牌已有 3 张暗刻，开杠潜力加分
        if (hand_counts[d] == 3) {
            ev -= 30.0; // 尽量不拆暗刻
        }

        adv.ev_score = ev;
        advices.push_back(adv);
    }

    // 综合期望 EV 降序排列
    std::sort(advices.begin(), advices.end(), [](const DiscardAdvice& a, const DiscardAdvice& b) {
        if (a.ev_score != b.ev_score) {
            return a.ev_score > b.ev_score;
        }
        return a.ukeire_live > b.ukeire_live;
    });

    return advices;
}

} // namespace mahjong
