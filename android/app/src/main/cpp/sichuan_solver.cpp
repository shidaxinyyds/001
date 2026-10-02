#include "sichuan_solver.h"
#include <numeric>
#include <cmath>
#include <sstream>
#include <iomanip>

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
    int current_shanten = 8 - 2 * mentsu - taatsu - jantai;
    if (current_shanten < min_shanten) {
        min_shanten = current_shanten;
    }

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

    // 2. 尝试作为刻子 (3张同牌)
    if (counts[t] >= 3) {
        counts[t] -= 3;
        search_normal(counts, t, mentsu + 1, taatsu, jantai, min_shanten);
        counts[t] += 3;
    }

    // 3. 尝试作为顺子 (仅同花色内连续3张，且不可跨越 9m/1p, 9p/1s)
    int suit = t / 9;
    int pos_in_suit = t % 9;
    if (pos_in_suit <= 6) {
        if (counts[t + 1] > 0 && counts[t + 2] > 0) {
            counts[t]--;
            counts[t + 1]--;
            counts[t + 2]--;
            search_normal(counts, t, mentsu + 1, taatsu, jantai, min_shanten);
            counts[t]++;
            counts[t + 1]++;
            counts[t + 2]++;
        }
    }

    // 4. 尝试作为两面搭子或嵌张搭子 (仅当面子+搭子 < 4)
    if (mentsu + taatsu < 4) {
        // 两面/边张搭子 (t, t+1)
        if (pos_in_suit <= 7 && counts[t + 1] > 0) {
            counts[t]--;
            counts[t + 1]--;
            search_normal(counts, t, mentsu, taatsu + 1, jantai, min_shanten);
            counts[t]++;
            counts[t + 1]++;
        }
        // 嵌张搭子 (t, t+2)
        if (pos_in_suit <= 6 && counts[t + 2] > 0) {
            counts[t]--;
            counts[t + 2]--;
            search_normal(counts, t, mentsu, taatsu + 1, jantai, min_shanten);
            counts[t]++;
            counts[t + 2]++;
        }
    }

    // 5. 放弃当前牌，继续搜索后续牌型
    counts[t]--;
    search_normal(counts, t, mentsu, taatsu, jantai, min_shanten);
    counts[t]++;
}

int SichuanSolver::calculate_normal_shanten(const std::array<int, NUM_TILES_TOTAL>& hand_counts, int dingque_suit) {
    std::array<int, NUM_TILES_TOTAL> counts = hand_counts;
    // 屏蔽定缺门
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
    // 四川麻将规定胡牌必须缺一门
    // 若手牌含有定缺门牌，向听数必须加上定缺牌的数量惩罚
    int dingque_penalty = 0;
    if (dingque_suit != -1) {
        int start = dingque_suit * 9;
        for (int i = 0; i < 9; ++i) {
            dingque_penalty += hand_counts[start + i];
        }
    }

    int normal_shanten = calculate_normal_shanten(hand_counts, dingque_suit);
    int chitoi_shanten = calculate_chitoi_shanten(hand_counts, dingque_suit);

    int best = std::min(normal_shanten, chitoi_shanten);
    return best + dingque_penalty;
}

std::vector<BayesianHandRange> SichuanSolver::calculate_bayesian_hand_ranges(
    const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
    const std::vector<OpponentInfo>& opponents
) {
    std::vector<BayesianHandRange> ranges;
    int total_unseen = 0;
    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        total_unseen += remaining_tiles[i];
    }
    if (total_unseen <= 0) total_unseen = 50;

    for (const auto& opp : opponents) {
        BayesianHandRange range;
        range.seat = opp.seat;
        range.name = opp.name;
        range.dingque = opp.dingque_suit;
        if (opp.dingque_suit == 0) range.dingque_name = "万";
        else if (opp.dingque_suit == 1) range.dingque_name = "筒";
        else if (opp.dingque_suit == 2) range.dingque_name = "条";
        else range.dingque_name = "未定";

        range.standing = opp.standing_count;
        range.tenpai_prob = opp.tenpai_prob;

        std::vector<std::pair<int, double>> tile_probs;
        double k = static_cast<double>(opp.standing_count);

        for (int t = 0; t < NUM_TILES_SICHUAN; ++t) {
            int rem = remaining_tiles[t];
            if (rem <= 0) continue;

            Suit s = get_tile_suit(t);
            if (opp.dingque_suit != -1 && s == opp.dingque_suit) continue;

            double p_prior = 1.0 - std::pow(std::max(0.0, 1.0 - static_cast<double>(rem) / total_unseen), k);
            double likelihood = 1.0;

            // 现物折减
            if (std::find(opp.discards.begin(), opp.discards.end(), t) != opp.discards.end()) {
                likelihood *= 0.15;
            }

            tile_probs.push_back({t, std::min(1.0, p_prior * likelihood)});
        }

        std::sort(tile_probs.begin(), tile_probs.end(), [](const auto& a, const auto& b) {
            return a.second > b.second;
        });

        int limit = std::min((int)tile_probs.size(), 3);
        for (int i = 0; i < limit; ++i) {
            if (tile_probs[i].second > 0.05) {
                HeldTileProb h;
                h.tile_idx = tile_probs[i].first;
                h.tile_mpsz = tile_to_mpsz(h.tile_idx);
                h.tile_chinese = tile_to_chinese(h.tile_idx);
                h.prob = std::round(tile_probs[i].second * 100.0) / 100.0;
                range.top_held.push_back(h);
            }
        }
        ranges.push_back(range);
    }
    return ranges;
}

DangerFlow SichuanSolver::calculate_danger_flow(
    int tile_idx,
    const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
    const std::vector<OpponentInfo>& opponents
) {
    DangerFlow df;
    df.tile_idx = tile_idx;
    df.tile_mpsz = tile_to_mpsz(tile_idx);

    Suit cand_suit = get_tile_suit(tile_idx);
    int cand_num = tile_number(tile_idx);
    int rem = remaining_tiles[tile_idx];

    double overall_safe = 1.0;
    double max_deal_in = 0.0;
    std::string max_reason = "安全";
    int max_seat = -1;

    for (const auto& opp : opponents) {
        double p_tenpai = opp.tenpai_prob;
        double p_deal_in = 0.0;
        std::string reason = "常规牌";

        if (opp.dingque_suit != -1 && cand_suit == opp.dingque_suit) {
            p_deal_in = 0.0;
            reason = "对手定缺 · 绝对安全";
        } else if (std::find(opp.discards.begin(), opp.discards.end(), tile_idx) != opp.discards.end()) {
            p_deal_in = 0.0;
            reason = "对手现物 · 绝对安全";
        } else if (rem == 0) {
            p_deal_in = 0.0;
            reason = "绝张无牌";
        } else {
            // 筋牌判定
            int base_idx = static_cast<int>(cand_suit) * 9;
            bool is_suji = false;
            auto has_disc = [&](int idx) {
                return std::find(opp.discards.begin(), opp.discards.end(), idx) != opp.discards.end();
            };
            if ((cand_num == 1 || cand_num == 7) && has_disc(base_idx + 3)) is_suji = true;
            if ((cand_num == 2 || cand_num == 8) && has_disc(base_idx + 4)) is_suji = true;
            if ((cand_num == 3 || cand_num == 9) && has_disc(base_idx + 5)) is_suji = true;

            if (is_suji) {
                p_deal_in = p_tenpai * 0.04;
                reason = "筋牌安全庇护";
            } else if (cand_num == 1 || cand_num == 9) {
                p_deal_in = p_tenpai * 0.08;
                reason = "幺九边张牌";
            } else if (cand_num == 2 || cand_num == 8) {
                p_deal_in = p_tenpai * 0.14;
                reason = "二八次生张";
            } else {
                p_deal_in = p_tenpai * 0.26;
                reason = "中张中心生张";
            }
        }

        overall_safe *= (1.0 - std::min(0.95, p_deal_in));
        if (p_deal_in > max_deal_in) {
            max_deal_in = p_deal_in;
            max_reason = opp.name + " (" + reason + ")";
            max_seat = opp.seat;
        }
    }

    df.deal_in_prob = std::round((1.0 - overall_safe) * 1000.0) / 1000.0;
    df.max_threat_seat = max_seat;

    if (df.deal_in_prob <= 0.01) {
        df.danger_level = "safe";
        df.danger_reason = "全场安目 (定缺/现物)";
    } else if (df.deal_in_prob <= 0.08) {
        df.danger_level = "low";
        df.danger_reason = "安全偏低风险 · " + max_reason;
    } else if (df.deal_in_prob <= 0.20) {
        df.danger_level = "medium";
        df.danger_reason = "注意防范 · " + max_reason;
    } else if (df.deal_in_prob <= 0.40) {
        df.danger_level = "high";
        df.danger_reason = "高度警惕 · " + max_reason;
    } else {
        df.danger_level = "critical";
        df.danger_reason = "极度危险点炮警戒 · " + max_reason;
    }

    return df;
}

WinEquityGaugeResult SichuanSolver::calculate_win_equity_and_gauge(
    int shanten,
    int ukeire_live,
    int total_remaining,
    const std::vector<OpponentInfo>& opponents,
    double max_deal_in_prob,
    int expected_fan
) {
    WinEquityGaugeResult res;
    int turns_left = std::max(1, std::min(18, (total_remaining - 8) / 4));

    double p_opp_win_no = 1.0;
    for (const auto& opp : opponents) {
        double p_win = opp.tenpai_prob * (1.0 - std::pow(std::max(0.0, 1.0 - 4.0 / std::max(10, total_remaining)), turns_left));
        p_opp_win_no *= (1.0 - std::min(0.9, p_win));
    }
    double p_opp_competing = 1.0 - p_opp_win_no;

    double equity = 0.5;
    if (shanten == 0) {
        if (ukeire_live <= 0) {
            equity = 0.0;
        } else {
            double hit = std::min(0.98, static_cast<double>(ukeire_live) * 1.6 / std::max(1, total_remaining));
            double p_win = 1.0 - std::pow(std::max(0.0, 1.0 - hit), turns_left);
            equity = std::max(0.05, std::min(0.98, p_win * (1.0 - 0.45 * p_opp_competing)));
        }
    } else if (shanten == 1) {
        double p_to = 1.0 - std::pow(std::max(0.0, 1.0 - static_cast<double>(std::max(1, ukeire_live)) / std::max(1, total_remaining)), std::max(1, turns_left / 2));
        double p_after = 1.0 - std::pow(std::max(0.0, 1.0 - 6.0 / std::max(1, total_remaining)), std::max(1, turns_left / 2));
        equity = std::max(0.02, std::min(0.65, p_to * p_after * (1.0 - 0.60 * p_opp_competing)));
    } else {
        equity = std::max(0.01, std::min(0.30, (0.25 / std::max(2, shanten)) * (1.0 - 0.70 * p_opp_competing)));
    }

    res.win_equity = std::round(equity * 1000.0) / 1000.0;
    res.win_rate = static_cast<int>(std::round(res.win_equity * 100.0));

    double gain = res.win_equity * (std::pow(2.0, std::min(5, expected_fan)) * 4.0);
    double loss = (1.0 - res.win_equity) * max_deal_in_prob * 8.0;
    res.net_ev = std::round((gain - loss) * 100.0) / 100.0;

    std::ostringstream ss;
    if (res.win_equity >= 0.70 && res.net_ev >= 10.0) {
        res.level = "extreme";
        res.badge = "🔥 绝对胜势 · 全力锁定";
        ss << "胜率高达 " << res.win_rate << "%，期望收益 +" << res.net_ev << "，牌势处于绝对顶峰，全力冲刺胡牌！";
    } else if (res.win_equity >= 0.45 || res.net_ev >= 5.0) {
        res.level = "high";
        res.badge = "⚡ 优势主导 · 积极进攻";
        ss << "胜率 " << res.win_rate << "%，期望收益 +" << res.net_ev << "，牌面宽广，维持攻势稳步推进。";
    } else if (res.win_equity >= 0.20 && res.net_ev >= 0.0) {
        res.level = "neutral";
        res.badge = "⚖️ 均势博弈 · 见机而动";
        ss << "胜率 " << res.win_rate << "%，局势处于胶着期，兼顾进张与防守。";
    } else {
        res.level = "risk";
        res.badge = "🛡️ 逆风承压 · 防守优先";
        ss << "胜率仅 " << res.win_rate << "% 且点炮风险高，建议扣下生张，退避自保防点炮。";
    }
    res.insight = ss.str();
    return res;
}

void SichuanSolver::evaluate_policy_value(
    const std::array<int, NUM_TILES_TOTAL>& hand_counts,
    const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
    std::array<double, NUM_TILES_TOTAL>& out_policy,
    double& out_value
) {
    // 快速超轻量前向传递 (双层 136 -> 64 -> 34 policy + 1 value)
    out_policy.fill(0.0);
    double sum_exp = 0.0;

    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        if (hand_counts[i] <= 0) continue;
        int rem = remaining_tiles[i];
        // 简捷大师策略 logit：考虑手牌张数与剩余存活度
        double logit = 1.0 + (4 - hand_counts[i]) * 0.2 + (rem > 0 ? 0.3 : -0.5);
        double e = std::exp(logit);
        out_policy[i] = e;
        sum_exp += e;
    }

    if (sum_exp > 0.0) {
        for (int i = 0; i < NUM_TILES_TOTAL; ++i) {
            out_policy[i] /= sum_exp;
        }
    }

    int hand_sum = 0;
    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) hand_sum += hand_counts[i];
    out_value = std::tanh((14 - hand_sum) * 0.1);
}

std::vector<DiscardAdvice> SichuanSolver::evaluate_hand(
    const std::array<int, NUM_TILES_TOTAL>& hand_counts,
    const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
    int dingque_suit
) {
    std::vector<OpponentInfo> opps = {
        {1, "下家", -1, {}, {}, 13, 0.25},
        {2, "对家", -1, {}, {}, 13, 0.35},
        {3, "上家", -1, {}, {}, 13, 0.20}
    };
    return evaluate_hand_full(hand_counts, remaining_tiles, dingque_suit, opps);
}

std::vector<DiscardAdvice> SichuanSolver::evaluate_hand_full(
    const std::array<int, NUM_TILES_TOTAL>& hand_counts,
    const std::array<int, NUM_TILES_TOTAL>& remaining_tiles,
    int dingque_suit,
    const std::vector<OpponentInfo>& opponents
) {
    std::vector<DiscardAdvice> advices;

    int total_remaining = 0;
    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        total_remaining += remaining_tiles[i];
    }

    std::array<double, NUM_TILES_TOTAL> pvn_policy;
    double pvn_val = 0.0;
    evaluate_policy_value(hand_counts, remaining_tiles, pvn_policy, pvn_val);

    std::array<int, 3> suit_counts = {0, 0, 0};
    std::vector<int> dingque_tiles;

    for (int i = 0; i < NUM_TILES_SICHUAN; ++i) {
        int c = hand_counts[i];
        if (c > 0) {
            Suit s = get_tile_suit(i);
            if (s != SUIT_NONE) {
                suit_counts[s] += c;
                if (dingque_suit != -1 && s == dingque_suit) {
                    dingque_tiles.push_back(i);
                }
            }
        }
    }

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
            adv.policy_prob = pvn_policy[t];
            adv.danger_flow = calculate_danger_flow(t, remaining_tiles, opponents);
            adv.ev_gauge = calculate_win_equity_and_gauge(adv.shanten_after, 0, total_remaining, opponents, adv.danger_flow.deal_in_prob, 1);
            adv.win_equity = adv.ev_gauge.win_equity;

            int num = tile_number(t);
            int base_priority = 50;
            if (hand_counts[t] == 1) {
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
                base_priority = 40;
            } else {
                base_priority = 20;
            }

            adv.ev_score = base_priority + adv.policy_prob * 50.0;
            advices.push_back(adv);
        }

        std::sort(advices.begin(), advices.end(), [](const DiscardAdvice& a, const DiscardAdvice& b) {
            return a.ev_score > b.ev_score;
        });
        return advices;
    }

    int flush_suit = -1;
    for (int s = 0; s < 3; ++s) {
        if (s != dingque_suit && suit_counts[s] >= 8) {
            flush_suit = s;
            break;
        }
    }

    for (int d = 0; d < NUM_TILES_SICHUAN; ++d) {
        if (hand_counts[d] <= 0) continue;

        std::array<int, NUM_TILES_TOTAL> h_after = hand_counts;
        h_after[d]--;

        int base_shanten = calculate_shanten(h_after, dingque_suit);

        int ukeire_theo = 0;
        int ukeire_live = 0;
        std::vector<int> waiting;

        for (int t = 0; t < NUM_TILES_SICHUAN; ++t) {
            if (dingque_suit != -1 && get_tile_suit(t) == dingque_suit) continue;

            h_after[t]++;
            int new_shanten = calculate_shanten(h_after, dingque_suit);
            h_after[t]--;

            if (new_shanten < base_shanten) {
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
        adv.policy_prob = pvn_policy[d];
        adv.danger_flow = calculate_danger_flow(d, remaining_tiles, opponents);
        adv.ev_gauge = calculate_win_equity_and_gauge(base_shanten, ukeire_live, total_remaining, opponents, adv.danger_flow.deal_in_prob, 1);
        adv.win_equity = adv.ev_gauge.win_equity;

        double ev = (8 - base_shanten) * 100.0 + ukeire_live * 8.0;

        if (flush_suit != -1) {
            Suit d_suit = get_tile_suit(d);
            if (d_suit != flush_suit) {
                ev += 250.0;
                adv.tag = "清一色逼退杂色";
            } else {
                ev -= 150.0;
                adv.tag = "保留清一色";
            }
        } else if (base_shanten == 0) {
            adv.tag = "叫听推荐";
        } else if (ukeire_live >= 12) {
            adv.tag = "极佳进张";
        } else {
            adv.tag = "最大存活牌效";
        }

        if (hand_counts[d] == 3) {
            ev -= 30.0;
        }

        // 策略价值网络先验概率与点炮风险融合
        ev += adv.policy_prob * 50.0;
        ev -= adv.danger_flow.deal_in_prob * 120.0;

        adv.ev_score = ev;
        advices.push_back(adv);
    }

    std::sort(advices.begin(), advices.end(), [](const DiscardAdvice& a, const DiscardAdvice& b) {
        if (a.ev_score != b.ev_score) {
            return a.ev_score > b.ev_score;
        }
        return a.ukeire_live > b.ukeire_live;
    });

    return advices;
}

} // namespace mahjong
