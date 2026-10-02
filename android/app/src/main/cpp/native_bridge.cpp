#include <jni.h>
#include <string>
#include <sstream>
#include <memory>
#include <android/log.h>

#include "sichuan_solver.h"
#include "tile_memory.h"
#include "game_fsm.h"

#define TAG "MahjongNative"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

static std::unique_ptr<mahjong::TileMemory> g_memory;
static std::unique_ptr<mahjong::GameFSM> g_fsm;
static int g_dingque_suit = -1;

extern "C" {

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeInit(JNIEnv* env, jclass clazz) {
    g_memory = std::make_unique<mahjong::TileMemory>();
    g_fsm = std::make_unique<mahjong::GameFSM>();
    g_dingque_suit = -1;
    LOGI("Mahjong Native Engine initialized successfully.");
}

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeReset(JNIEnv* env, jclass clazz) {
    if (g_memory) g_memory->reset();
    if (g_fsm) g_fsm->reset();
    g_dingque_suit = -1;
    LOGI("Mahjong Native Engine reset.");
}

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeSetDingque(JNIEnv* env, jclass clazz, jint suit) {
    g_dingque_suit = (suit >= 0 && suit <= 2) ? suit : -1;
    LOGI("Mahjong Native Engine set Dingque: %d", g_dingque_suit);
}

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeRecordDiscard(JNIEnv* env, jclass clazz, jint tile_idx, jint count) {
    if (g_memory) {
        g_memory->add_discard(tile_idx, count);
    }
}

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeRecordMeld(JNIEnv* env, jclass clazz, jint tile_idx, jint count) {
    if (g_memory) {
        g_memory->add_meld(tile_idx, count);
    }
}

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeSetOpponentDingque(JNIEnv* env, jclass clazz, jint seat, jint suit) {
    if (g_memory) {
        g_memory->set_opponent_dingque(seat, suit);
    }
}

JNIEXPORT void JNICALL
Java_com_example_auto_vision_NativeEngine_nativeRecordOpponentDiscard(JNIEnv* env, jclass clazz, jint seat, jint tile_idx) {
    if (g_memory) {
        g_memory->add_opponent_discard(seat, tile_idx);
    }
}

JNIEXPORT jstring JNICALL
Java_com_example_auto_vision_NativeEngine_nativeEvaluate(
    JNIEnv* env,
    jclass clazz,
    jintArray hand_tiles_arr,
    jint visual_dq_suit,
    jboolean is_swap,
    jboolean is_dq,
    jboolean is_table
) {
    if (!g_memory || !g_fsm) {
        return env->NewStringUTF("{\"status\":\"error\",\"message\":\"Engine not initialized\"}");
    }

    // 1. 解析传入的手牌数组 (tile_idx 0..26)
    std::array<int, mahjong::NUM_TILES_TOTAL> hand_counts;
    hand_counts.fill(0);

    jsize len = 0;
    std::string hand_mpsz = "";
    if (hand_tiles_arr != nullptr) {
        len = env->GetArrayLength(hand_tiles_arr);
        jint* elements = env->GetIntArrayElements(hand_tiles_arr, nullptr);
        for (int i = 0; i < len; ++i) {
            int t = elements[i];
            if (t >= 0 && t < mahjong::NUM_TILES_SICHUAN) {
                hand_counts[t]++;
                hand_mpsz += mahjong::tile_to_mpsz(t);
            }
        }
        env->ReleaseIntArrayElements(hand_tiles_arr, elements, JNI_ABORT);
    }

    // 定缺门优先级: 手动覆盖 > 视觉识别徽章
    int active_dq = g_dingque_suit;
    if (active_dq == -1 && visual_dq_suit >= 0 && visual_dq_suit <= 2) {
        active_dq = visual_dq_suit;
    }

    // 2. 更新单向锁步状态机
    mahjong::GameState state = g_fsm->update(
        len,
        is_swap,
        is_dq,
        active_dq,
        is_table
    );

    if (g_fsm->should_reset_memory()) {
        g_memory->reset();
        g_fsm->acknowledge_reset();
    }

    // 3. 记牌器更新与出牌瞬间感知
    int discarded_tile = g_memory->update_hand(hand_counts);

    // 4. 真实剩余牌量获取
    auto remaining = g_memory->get_remaining_tiles();
    int total_remaining = g_memory->get_total_remaining();

    // 5. 组装对手状态模型
    std::vector<mahjong::OpponentInfo> opponents;
    for (int seat = 1; seat <= 3; ++seat) {
        mahjong::OpponentInfo opp;
        opp.seat = seat;
        opp.name = (seat == 1 ? "下家" : (seat == 2 ? "对家" : "上家"));
        opp.dingque_suit = g_memory->get_opponent_dingque(seat);
        opp.discards = g_memory->get_opponent_discards(seat);
        opp.melds = g_memory->get_opponent_melds(seat);
        opp.standing_count = std::max(1, 13 - static_cast<int>(opp.melds.size()));
        opp.tenpai_prob = std::min(0.90, 0.20 + opp.discards.size() * 0.03 + (opp.melds.size() / 3) * 0.20);
        opponents.push_back(opp);
    }

    // 6. 求解器运算 (定缺门控 + 真实余牌牌效 + EV 期望 + 贝叶斯透视 + 胜率雷达 + PVN)
    int current_shanten = mahjong::SichuanSolver::calculate_shanten(hand_counts, active_dq);
    auto advices = mahjong::SichuanSolver::evaluate_hand_full(hand_counts, remaining, active_dq, opponents);
    auto hand_ranges = mahjong::SichuanSolver::calculate_bayesian_hand_ranges(remaining, opponents);

    // 7. 构造高性能紧凑 JSON
    std::string status = "ok";
    if (state == mahjong::STATE_IDLE || state == mahjong::STATE_SETTLED) {
        status = (len == 0) ? "waiting" : "ok";
    } else if (state == mahjong::STATE_SWAP) {
        status = "swap";
    } else if (state == mahjong::STATE_DINGQUE) {
        status = "dingque";
    } else if (state == mahjong::STATE_PLAYING) {
        status = (len == 0) ? "no_tiles" : "ok";
    }

    std::ostringstream ss;
    ss << "{";
    ss << "\"mode\":\"sc\",";
    ss << "\"state\":\"" << g_fsm->get_state_string() << "\",";
    ss << "\"status\":\"" << status << "\",";
    ss << "\"count\":" << len << ",";
    ss << "\"hand\":\"" << hand_mpsz << "\",";
    ss << "\"dingque\":" << (active_dq >= 0 ? std::to_string(active_dq) : "null") << ",";
    ss << "\"dingque_suit\":" << (active_dq >= 0 ? std::to_string(active_dq) : "null") << ",";
    if (active_dq == 0) ss << "\"dingque_name\":\"万\",";
    else if (active_dq == 1) ss << "\"dingque_name\":\"筒\",";
    else if (active_dq == 2) ss << "\"dingque_name\":\"条\",";
    else ss << "\"dingque_name\":null,";
    ss << "\"swap_phase\":" << (state == mahjong::STATE_SWAP ? "true" : "false") << ",";
    ss << "\"dingque_phase\":" << (state == mahjong::STATE_DINGQUE ? "true" : "false") << ",";
    ss << "\"shanten\":" << current_shanten << ",";
    ss << "\"remaining\":" << total_remaining << ",";
    ss << "\"inferred_discard\":" << discarded_tile << ",";

    if (status == "waiting") {
        ss << "\"message\":\"等待牌局开始\",";
    } else if (status == "swap") {
        ss << "\"message\":\"换牌阶段推演中\",";
    } else if (status == "dingque") {
        ss << "\"message\":\"定缺选门推演中\",";
    } else if (status == "no_tiles") {
        ss << "\"message\":\"\",";
    } else {
        ss << "\"message\":\"手牌已就绪\",";
    }

    // 胜率与 EV 仪表盘
    double top_win_equity = advices.empty() ? 0.5 : advices[0].win_equity;
    ss << "\"win_equity\":" << top_win_equity << ",";
    if (!advices.empty()) {
        const auto& eg = advices[0].ev_gauge;
        ss << "\"ev_gauge\":{";
        ss << "\"win_equity\":" << eg.win_equity << ",";
        ss << "\"win_rate\":" << eg.win_rate << ",";
        ss << "\"net_ev\":" << eg.net_ev << ",";
        ss << "\"level\":\"" << eg.level << "\",";
        ss << "\"badge\":\"" << eg.badge << "\",";
        ss << "\"insight\":\"" << eg.insight << "\"";
        ss << "},";
    }

    // 最佳出牌推荐
    if (!advices.empty()) {
        const auto& best = advices[0];
        ss << "\"best\":\"" << best.tile_mpsz << "\",";
        ss << "\"best_chinese\":\"" << best.tile_chinese << "\",";
        ss << "\"best_ukeire\":" << best.ukeire_live << ",";
        ss << "\"best_tag\":\"" << best.tag << "\",";
        ss << "\"danger_flow\":{";
        ss << "\"tile\":\"" << best.danger_flow.tile_mpsz << "\",";
        ss << "\"deal_in_prob\":" << best.danger_flow.deal_in_prob << ",";
        ss << "\"danger_level\":\"" << best.danger_flow.danger_level << "\",";
        ss << "\"danger_reason\":\"" << best.danger_flow.danger_reason << "\"";
        ss << "},";
    } else {
        ss << "\"best\":\"\",";
        ss << "\"best_chinese\":\"\",";
        ss << "\"best_ukeire\":0,";
        ss << "\"best_tag\":\"\",";
    }

    // 出牌推荐列表 Top 5 (每项包含 policy_prob, win_equity, danger_flow)
    ss << "\"advice\":[";
    int adv_limit = std::min((int)advices.size(), 5);
    for (int i = 0; i < adv_limit; ++i) {
        if (i > 0) ss << ",";
        const auto& a = advices[i];
        ss << "{";
        ss << "\"tile\":\"" << a.tile_mpsz << "\",";
        ss << "\"chinese\":\"" << a.tile_chinese << "\",";
        ss << "\"shanten\":" << a.shanten_after << ",";
        ss << "\"ukeire\":" << a.ukeire_live << ",";
        ss << "\"ev\":" << (int)a.ev_score << ",";
        ss << "\"tag\":\"" << a.tag << "\",";
        ss << "\"reason\":\"" << a.tag << " · 进张" << a.ukeire_live << "张\",";
        ss << "\"is_dingque\":" << (a.is_dingque ? "true" : "false") << ",";
        ss << "\"policy_prob\":" << a.policy_prob << ",";
        ss << "\"win_equity\":" << a.win_equity << ",";
        ss << "\"danger_flow\":{";
        ss << "\"tile\":\"" << a.danger_flow.tile_mpsz << "\",";
        ss << "\"deal_in_prob\":" << a.danger_flow.deal_in_prob << ",";
        ss << "\"danger_level\":\"" << a.danger_flow.danger_level << "\",";
        ss << "\"danger_reason\":\"" << a.danger_flow.danger_reason << "\"";
        ss << "}";
        ss << "}";
    }
    ss << "],";

    // 贝叶斯对手手牌透视
    ss << "\"hand_ranges\":[";
    for (size_t i = 0; i < hand_ranges.size(); ++i) {
        if (i > 0) ss << ",";
        const auto& hr = hand_ranges[i];
        ss << "{";
        ss << "\"seat\":" << hr.seat << ",";
        ss << "\"name\":\"" << hr.name << "\",";
        ss << "\"dingque\":" << hr.dingque << ",";
        ss << "\"dingque_name\":\"" << hr.dingque_name << "\",";
        ss << "\"standing\":" << hr.standing << ",";
        ss << "\"tenpai_prob\":" << hr.tenpai_prob << ",";
        ss << "\"top_held\":[";
        for (size_t j = 0; j < hr.top_held.size(); ++j) {
            if (j > 0) ss << ",";
            ss << "{\"tile\":\"" << hr.top_held[j].tile_mpsz << "\",\"chinese\":\""
               << hr.top_held[j].tile_chinese << "\",\"prob\":" << hr.top_held[j].prob << "}";
        }
        ss << "]";
        ss << "}";
    }
    ss << "],";

    // 9x3 剩余牌矩阵
    ss << "\"remaining_matrix\":" << g_memory->get_remaining_matrix_json() << ",";

    // 牌势感知与军师安抚 (Mood Guard)
    int top_ukeire = advices.empty() ? 0 : advices[0].ukeire_live;
    ss << "\"mood\":{";
    if (current_shanten >= 3 || top_ukeire <= 2) {
        ss << "\"state\":\"defensive\",";
        ss << "\"badge\":\"🛡️ 逆风抗压 · 防守保分\",";
        ss << "\"desc\":\"起手牌型较散（处于摸牌波谷），切忌急躁，优先扣下生张稳扎稳打。\",";
        ss << "\"level\":\"orange\"";
    } else if (current_shanten == 0 || (current_shanten == 1 && top_ukeire >= 8) || top_ukeire >= 12) {
        ss << "\"state\":\"favorable\",";
        ss << "\"badge\":\"🌊 牌势顺遂 · 乘胜追击\",";
        if (current_shanten == 0) {
            ss << "\"desc\":\"已达听牌绝佳状态！牌势凌厉，全力锁定胡牌张，乘胜追击！\",";
        } else {
            ss << "\"desc\":\"一向听优质大进张，进张面极宽，全力冲刺下叫！\",";
        }
        ss << "\"level\":\"green\"";
    } else {
        ss << "\"state\":\"steady\",";
        ss << "\"badge\":\"⚖️ 局势平稳 · 见机行事\",";
        ss << "\"desc\":\"当前牌局平稳推进中，进张面均衡，保持节奏等待良机。\",";
        ss << "\"level\":\"blue\"";
    }
    ss << "}";

    ss << "}";

    std::string json_str = ss.str();
    return env->NewStringUTF(json_str.c_str());
}

} // extern "C"
