/**
 * And64InlineHook.h — 轻量级 ARM64 内联 Hook 库
 *
 * 用途: 在直装(免root)模式下 Hook eglSwapBuffers, 在游戏渲染帧时绘制 ImGui 菜单
 *
 * 原理:
 *   1. 在目标函数入口处写入跳转指令 (LDR X16, =hook; BR X16), 跳到我们的 Hook 函数
 *   2. 创建一个 trampoline(跳板), 保存被覆盖的原始指令 + 跳回原函数
 *   3. 调用原始函数时, 实际调用 trampoline, 先执行原始指令再跳回
 *
 * 限制: 目标函数前 4 条指令(16字节)不能包含 PC 相对寻址指令(B/BL/ADR/ADRP/LDR literal)
 *       对于 eglSwapBuffers 等标准函数, 函数序言通常是 STP/MOV, 不受此限制
 *
 * 编译: 仅支持 arm64-v8a (NDK)
 */

#ifndef AND64_INLINE_HOOK_H
#define AND64_INLINE_HOOK_H

#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <android/log.h>

#define HOOK_TAG "And64Hook"
#define HOOK_LOGI(...) __android_log_print(ANDROID_LOG_INFO,  HOOK_TAG, __VA_ARGS__)
#define HOOK_LOGE(...) __android_log_print(ANDROID_LOG_ERROR, HOOK_TAG, __VA_ARGS__)

// ---- ARM64 跳转指令编码 ----
// LDR X16, #8  → 从 PC+8 处加载 8 字节地址到 X16
// 编码: 0101 1000 0000 0000 0000 0000 0101 0000 = 0x58000050
#define LDR_X16_8  0x58000050U
// BR X16 → 跳转到 X16 中存储的地址
// 编码: 1101 0110 0001 1111 0000 0010 0000 0000 = 0xD61F0200
#define BR_X16     0xD61F0200U

// Hook 补丁大小: 4(LDR) + 4(BR) + 8(地址) = 16 字节 = 4 条 ARM64 指令
#define HOOK_PATCH_SIZE 16

/**
 * 安装 ARM64 内联 Hook
 *
 * @param target   要 Hook 的目标函数地址 (例如 dlsym 返回的 eglSwapBuffers)
 * @param hook     我们的替换函数地址
 * @param original 输出: 指向 trampoline 的指针, 调用它等于调用原始函数
 * @return true=成功, false=失败
 */
static bool A64HookFunction(void *target, void *hook, void **original)
{
    if (!target || !hook) {
        HOOK_LOGE("参数为空: target=%p hook=%p", target, hook);
        return false;
    }

    long page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) page_size = 4096;

    // ---- 1. 分配 trampoline (可执行内存页) ----
    void *trampoline = mmap(nullptr, page_size,
                            PROT_READ | PROT_WRITE | PROT_EXEC,
                            MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
    if (trampoline == MAP_FAILED) {
        HOOK_LOGE("mmap trampoline 失败");
        return false;
    }

    // ---- 2. 复制被覆盖的原始指令到 trampoline ----
    memcpy(trampoline, target, HOOK_PATCH_SIZE);

    // ---- 3. 在 trampoline 末尾追加跳回指令 (回到 target + HOOK_PATCH_SIZE) ----
    uint8_t *jump_back = (uint8_t *)trampoline + HOOK_PATCH_SIZE;
    *(uint32_t *)(jump_back + 0) = LDR_X16_8;   // LDR X16, #8
    *(uint32_t *)(jump_back + 4) = BR_X16;       // BR  X16
    *(uint64_t *)(jump_back + 8) = (uint64_t)target + HOOK_PATCH_SIZE;

    if (original) *original = trampoline;

    // ---- 4. 将 target 所在页改为可读写可执行 ----
    uintptr_t target_page = (uintptr_t)target & ~(page_size - 1);
    if (mprotect((void *)target_page, page_size * 2,
                 PROT_READ | PROT_WRITE | PROT_EXEC) != 0) {
        HOOK_LOGE("mprotect 失败, target=%p", target);
        munmap(trampoline, page_size);
        return false;
    }

    // ---- 5. 在 target 入口写入跳转到 hook 的指令 ----
    uint8_t *patch = (uint8_t *)target;
    *(uint32_t *)(patch + 0) = LDR_X16_8;        // LDR X16, #8
    *(uint32_t *)(patch + 4) = BR_X16;            // BR  X16
    *(uint64_t *)(patch + 8) = (uint64_t)hook;    // hook 函数地址 (8字节)

    // ---- 6. 恢复 target 页权限 ----
    mprotect((void *)target_page, page_size * 2, PROT_READ | PROT_EXEC);

    // ---- 7. 刷新指令缓存 (ARM64 必须手动刷新 icache) ----
    __builtin___clear_cache((char *)target, (char *)target + HOOK_PATCH_SIZE);
    __builtin___clear_cache((char *)trampoline, (char *)trampoline + page_size);

    HOOK_LOGI("Hook 安装成功: %p -> %p (trampoline=%p)", target, hook, trampoline);
    return true;
}

#endif // AND64_INLINE_HOOK_H
