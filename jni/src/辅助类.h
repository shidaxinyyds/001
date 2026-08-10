#ifndef 辅助类_H
#define 辅助类_H

#include <dirent.h>
#include <pthread.h>
#include <regex.h>
#include <stdio.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <vector>
#include <algorithm>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
#include "./CPUaffinity/timer.h"
#include "obfuscate.h"
#include "driver.h"

#define PI 3.141592653589793238

class Kernel {
   private:
    int has_upper = 0;
    int has_lower = 0;
    int has_symbol = 0;
    int has_digit = 0;
    int fd;
    pid_t pid;
    int 选择值;  // 新增：驱动选择值
    
    // ==================== 移植：Hook系列驱动结构体 ====================
    typedef struct _COPY_MEMORY {
        pid_t pid;
        uintptr_t addr;
        void* buffer;
        size_t size;
    } COPY_MEMORY, *PCOPY_MEMORY;
    
    // ==================== 新增：Dev方案驱动专用 ====================
    // Dev方案驱动查找函数（移植自第一份文件）
    char* dev_Sch() {
        const char* dev_path = "/dev";
        DIR* dir = opendir(dev_path);
        if (dir == NULL) {
            printf("无法打开/dev目录\n");
            return NULL;
        }

        struct dirent* entry;
        char file_path[256];
        while ((entry = readdir(dir)) != NULL) {
            if (strstr(entry->d_name, "std") != NULL || 
                strcmp(entry->d_name, ".") == 0 || 
                strcmp(entry->d_name, "..") == 0 || 
                strstr(entry->d_name, "gpiochip") != NULL) {
                continue;
            }

            if (strchr(entry->d_name, '_') != NULL && 
                strchr(entry->d_name, '-') != NULL && 
                strchr(entry->d_name, ':') != NULL) {
                continue;
            }
            
            sprintf(file_path, "%s/%s", dev_path, entry->d_name);

            struct stat file_info;
            if (stat(file_path, &file_info) < 0)
                continue;

            if ((localtime(&file_info.st_ctime)->tm_year + 1900) <= 1980)
                continue;

            if (strlen(entry->d_name) > 7 || strlen(entry->d_name) < 5)
                continue;

            if (file_info.st_gid != 0 || file_info.st_uid != 0)
                continue;

            if (S_ISCHR(file_info.st_mode) || S_ISBLK(file_info.st_mode)) {
                if (file_info.st_gid == 0 && file_info.st_uid == 0) {
                    printf("%s\n", file_path);
                    char* devpath = (char*)malloc(32);
                    strcpy(devpath, file_path);
                    closedir(dir);
                    return devpath;
                }
            }
        }
        closedir(dir);
        return NULL;
    }
    
    // ioctl打开驱动节点函数（移植自第一份文件）
    int ioctl_str(const char* path) {
        int bsf = open(path, O_RDWR);
        if (bsf == -1) {
            return -1;
        }
        return bsf;
    }
    
    // 驱动操作枚举（移植自第一份文件，适配Hook和Dev）
    enum OPERATIONS {
        OP_INIT_KEY = 0x800,
        OP_READ_MEM = 0x801,
        OP_WRITE_MEM = 0x802,
        OP_MODULE_BASE = 0x803,
        OP_HIDE_PROCESS = 0x804,
        // Hook系列专用命令
        HOOK_READ_MEM = 601,    // Hook读内存命令
        HOOK_WRITE_MEM = 602    // Hook写内存命令
    };

    // Hook系列驱动操作函数
    bool hook_read_memory(uintptr_t addr, void* buffer, size_t size) {
        if (fd == -1) return false;
        
        COPY_MEMORY cm;
        cm.pid = this->pid;
        cm.addr = addr;
        cm.buffer = buffer;
        cm.size = size;
        
        // Hook系列使用命令601读取内存
        if (ioctl(fd, HOOK_READ_MEM, &cm) != 0) {
            return false;
        }
        return true;
    }
    
    bool hook_write_memory(uintptr_t addr, void* buffer, size_t size) {
        if (fd == -1) return false;
        
        COPY_MEMORY cm;
        cm.pid = this->pid;
        cm.addr = addr;
        cm.buffer = buffer;
        cm.size = size;
        
        // Hook系列使用命令602写入内存
        if (ioctl(fd, HOOK_WRITE_MEM, &cm) != 0) {
            return false;
        }
        return true;
    }
    
    // Dev方案驱动操作函数
    bool dev_read_memory(uintptr_t addr, void* buffer, size_t size) {
        if (fd == -1) return false;
        
        COPY_MEMORY cm;
        cm.pid = this->pid;
        cm.addr = addr & 0xFFFFFFFFFFFF;  // Dev方案需要地址掩码
        cm.buffer = buffer;
        cm.size = size;
        
        // Dev方案使用标准命令读取内存
        if (ioctl(fd, OP_READ_MEM, &cm) != 0) {
            return false;
        }
        return true;
    }
    
    bool dev_write_memory(uintptr_t addr, void* buffer, size_t size) {
        if (fd == -1) return false;
        
        COPY_MEMORY cm;
        cm.pid = this->pid;
        cm.addr = addr & 0xFFFFFFFFFFFF;  // Dev方案需要地址掩码
        cm.buffer = buffer;
        cm.size = size;
        
        // Dev方案使用标准命令写入内存
        if (ioctl(fd, OP_WRITE_MEM, &cm) != 0) {
            return false;
        }
        return true;
    }

   public:
    // 构造函数：初始化驱动选择
    Kernel() {
        fd = -1;
       /* printf("\033[34;1m");
        printf("[-]0------RT HOOK(也支持GT2.2)\n");
        printf("[-]1------RT DEV(也支持GT1.7)\n");
        printf("[-]请选择刷入的驱动:");
        scanf("%d", &选择值);*/

        选择值 =0;
        
        if (选择值 == 0) {
            // Hook系列驱动初始化
            printf("执行Hook驱动检测中……\n");
            // Hook系列使用socket方式
            fd = socket(AF_INET, SOCK_DGRAM, 0);
            if (fd == -1) {
                perror("[-] Hook驱动打开失败");
                exit(EXIT_FAILURE);
            }
            printf("Hook驱动对接成功\n");
        }
        else if (选择值 == 1) {
            // Dev方案驱动初始化
            printf("Dev方案驱动初始化中...\n");
            char* Devstr = this->dev_Sch();
            if (Devstr == NULL) {
                printf("未寻找到Dev方案驱动\n");
                exit(1);
            }
            else {
                fd = this->ioctl_str(Devstr);
                if (fd > 0) {
                    printf("驱动节点%s，执行驱动过检测中…\n", Devstr);
                    free(Devstr);
                    printf("Dev驱动对接成功\n");
                } else {
                    printf("Dev驱动打开失败\n");
                    free(Devstr);
                    exit(1);
                }
            }
        }
        //else if (选择值 == 3)
        //{
        //    kpm_driver = std::make_unique<Driver>();

        //    // 程序随机运行在CPU0-4上，并且选择使用率最低的那颗CPU核心
        //    driver->cpuset(0, 4);


        //}
        else {
            printf("输入错误,请重新输入\n");
            exit(1);
        }
    }
    
    ~Kernel() {
        if (fd != -1) {
            close(fd);
        }
    }
    
    void 初始化读写(int pid) { 
        this->pid = pid; 
    }
    
    // 统一的读取内存函数
    bool readv(uintptr_t addr, void* buffer, size_t size) {
        if (this->选择值 == 0) {
            // Hook系列驱动
            return hook_read_memory(addr, buffer, size);
        }
        else if (this->选择值 == 1) {
            // Dev方案驱动
            return dev_read_memory(addr, buffer, size);
        }
        return false;
    }
    
    // 统一的写入内存函数
    bool writev(uintptr_t addr, void* buffer, size_t size) {
        if (this->选择值 == 0) {
            // Hook系列驱动
            return hook_write_memory(addr, buffer, size);
        }
        else if (this->选择值 == 1) {
            // Dev方案驱动
            return dev_write_memory(addr, buffer, size);
        }
        return false;
    }
    
    template <typename T>
    T Read(uintptr_t address) {
        T res;
        if (this->readv(address, &res, sizeof(T)))
            return res;
        return {};
    }
    
    // 获取模块基址函数（保持不变）
    uintptr_t get_module_base(char* name) {
        FILE* fp; 
        long addr = 0; 
        char* pch; 
        char filename[64]; 
        char line[1024]; 
        snprintf(filename, sizeof(filename), "/proc/%d/maps", this->pid); 
        fp = fopen(filename, "r"); 
        if (fp != NULL) { 
            while (fgets(line, sizeof(line), fp)) { 
                if (strstr(line, name)) { 
                    pch = strtok(line, "-"); 
                    addr = strtoul(pch, NULL, 16); 
                    if (addr == 0x8000) addr = 0; 
                        break; 
                } 
            } 
            fclose(fp); 
        } 
        return addr; 
    }
    
    uintptr_t getPtr64(uintptr_t addr) {
        unsigned long var = 0;
        readv(addr, &var, 8);
        return (var);
    }
    
    uintptr_t getPtr32(uintptr_t addr) {
        unsigned int var = 0;
        readv(addr & 0xFFFFFFFFFF, &var, 4);
        return (var & 0xFFFFFFFFFF);
    }
    
    float getFloat(uintptr_t addr) {
        float var = 0;
        readv(addr, &var, 4);
        return var;
    }
    
    int getDword(uintptr_t addr) {
        int var = 0;
        readv(addr, &var, 4);
        return var;
    }
    
    int WriteDword(long int addr, int value) {
        return writev(addr, &value, 4) ? 1 : 0;
    }
    
    float WriteFloat(long int addr, float value) {
        return writev(addr, &value, 4) ? value : 0.0f;
    }
    
    void writefloat(unsigned long addr, float data) {
        writev(addr, &data, 4);
    }
    
    void writeptr(unsigned long addr, uintptr_t data) {
        writev(addr, &data, 8);
    }
    
    void writedword(unsigned long addr, int data) {
        writev(addr, &data, 4);
    }
    
    int getPID(const char* packageName) {
        FILE* fp;
        pid_t pid;
        char cmd[0x100] = "pidof ";
        strcat(cmd, packageName);
        fp = popen(cmd, "r");
        fscanf(fp, "%d", &pid);
        pclose(fp);
        return pid;
    }
    
    void getUTF8(char* buf, unsigned long namepy) {
        unsigned short buf16[16] = {0};
        readv(namepy, buf16, 28);
        unsigned short* pTempUTF16 = buf16;
        char* pTempUTF8 = buf;
        char* pUTF8End = pTempUTF8 + 32;
        while (pTempUTF16 < pTempUTF16 + 28) {
            if (*pTempUTF16 <= 0x007F && pTempUTF8 + 1 < pUTF8End) {
                *pTempUTF8++ = (char)*pTempUTF16;
            } else if (*pTempUTF16 >= 0x0080 && *pTempUTF16 <= 0x07FF && pTempUTF8 + 2 < pUTF8End) {
                *pTempUTF8++ = (*pTempUTF16 >> 6) | 0xC0;
                *pTempUTF8++ = (*pTempUTF16 & 0x3F) | 0x80;
            } else if (*pTempUTF16 >= 0x0800 && *pTempUTF16 <= 0xFFFF && pTempUTF8 + 3 < pUTF8End) {
                *pTempUTF8++ = (*pTempUTF16 >> 12) | 0xE0;
                *pTempUTF8++ = ((*pTempUTF16 >> 6) & 0x3F) | 0x80;
                *pTempUTF8++ = (*pTempUTF16 & 0x3F) | 0x80;
            } else {
                break;
            }
            pTempUTF16++;
        }
    }
    
    bool init_key(char* key) {
        char buf[0x100];
        strcpy(buf, key);
        if (ioctl(fd, OP_INIT_KEY, buf) != 0) {
            return false;
        }
        return true;
    }
    
    // 以下两个函数保持原样，未做修改
    char* driver_path();
    int symbol_file(const char* filename);
    char getByte(unsigned long addr);
};

#include "Draw.h"
#include "imgui_impl_opengl3.h"
#include "结构体.h"
#include "骨骼.hpp"



class StringFloatMap {
   private:
    map<string, vector<float>> data;

   public:
    void add(string key, float value1, float value2) {
        vector<float> values;
        values.push_back(value1);
        values.push_back(value2);
        data[key] = values;
    }

    void remove(string key) {
        data.erase(key);
    }

    bool exists(string key) {
        return data.find(key) != data.end();
    }

    string calculateKey(float 坐标X, float 坐标Y) {
        int maps = 999;
        string 键名;
        for (const auto& pair : data) {
            const vector<float>& values = pair.second;
            int result = (int)sqrt(pow(坐标X - values[0], 2) + pow(坐标Y - values[1], 2)) * 0.01;
            // printf("测试值:%f \n",result);
            if (result < maps) {
                // return pair.first;
                maps = result;
                键名 = pair.first;
                // break;
            }
        }
        if (maps < 20) {
            return 键名;
        }
        return "";  // 如果没有满足条件的键名，则返回空字符串
    }
};

class Timer {
   private:
    std::map<std::string, int> timers;

   public:
    void addTimer(const std::string& name, int seconds) {
        if (timers.find(name) == timers.end()) {
            timers[name] = seconds;
        }
    }

    void updateTimers() {
        for (auto& timer : timers) {
            timer.second++;
        }
    }

    void checkAndRemoveTimers() {
        for (auto it = timers.begin(); it != timers.end();) {
            if (it->second == 1500) {
                it = timers.erase(it);
            } else {
                ++it;
            }
        }
    }

    int getTimerSeconds(const std::string& name) {
        auto it = timers.find(name);
        if (it != timers.end()) {
            return it->second;
        }
        return -1;
    }

    void removeTimer(const std::string& name) {
        timers.erase(name);
    }

    bool hasTimer(const std::string& name) {  // 判断是否存在
        return timers.find(name) != timers.end();
    }

    void renameTimer(const std::string& oldName, const std::string& newName) {
        if (timers.find(oldName) != timers.end()) {
            timers[newName] = timers[oldName];
            timers.erase(oldName);
        }
    }
};

class 计算 {
   public:
    int 计算距离(D3DVector 自身坐标, D3DVector 对方坐标);
    D4DVector 计算屏幕坐标(float 矩阵[16], D3DVector 人物坐标, float px, float py);
    D2DVector 计算屏幕坐标2(float 矩阵[16], D3DVector 人物坐标, float px, float py);
    float 计算屏幕距离(D2DVector& 坐标, float px, float py);
    float 计算目标屏幕高度(float 距离, int 目标高度);
    骨骼数据 计算骨骼(float (&矩阵)[16], D3DVector (&骨骼)[17], float px, float py);
    D2DVector rotateCoord(float angle, float objRadar_x, float objRadar_y);
    D2DVector rotateCoord(D3DVector Enemy, D3DVector RealPerson);
};
// 绘图
class 绘图 {
    struct 颜色 {
        ImColor 红色 = ImColor(255, 0, 0, 255);
        ImColor 白色 = ImColor(255, 255, 255, 255);
        ImColor 蓝色 = ImColor(0, 0, 255, 255);
        ImColor 绿色 = ImColor(0, 255, 0, 255);
        ImColor 黄色 = ImColor(255, 255, 0, 255);
        ImColor 黑色 = ImColor(0, 0, 0, 255);
    };

   public:
    struct VecTor2 {
        float x;
        float y;
        VecTor2() {
            this->x = 0;
            this->y = 0;
        }
        VecTor2(float x, float y) {
            this->x = x;
            this->y = y;
        }
        bool operator!=(const VecTor2& Pos) {
            if (this->x != Pos.x || this->y != Pos.y) {
                return true;
            }
            return false;
        }
        VecTor2 operator+(float v) const {
            return VecTor2(x + v, y + v);
        }
        VecTor2 operator-(float v) const {
            return VecTor2(x - v, y - v);
        }
        VecTor2 operator*(float v) const {
            return VecTor2(x * v, y * v);
        }
        VecTor2 operator/(float v) const {
            return VecTor2(x / v, y / v);
        }
        VecTor2& operator+=(float v) {
            x += v;
            y += v;
            return *this;
        }
        VecTor2& operator-=(float v) {
            x -= v;
            y -= v;
            return *this;
        }
        VecTor2& operator*=(float v) {
            x *= v;
            y *= v;
            return *this;
        }
        VecTor2& operator/=(float v) {
            x /= v;
            y /= v;
            return *this;
        }
        VecTor2 operator+(const VecTor2& v) const {
            return VecTor2(x + v.x, y + v.y);
        }
        VecTor2 operator-(const VecTor2& v) const {
            return VecTor2(x - v.x, y - v.y);
        }
        VecTor2 operator*(const VecTor2& v) const {
            return VecTor2(x * v.x, y * v.y);
        }
        VecTor2 operator/(const VecTor2& v) const {
            return VecTor2(x / v.x, y / v.y);
        }
        VecTor2& operator+=(const VecTor2& v) {
            x += v.x;
            y += v.y;
            return *this;
        }
        VecTor2& operator-=(const VecTor2& v) {
            x -= v.x;
            y -= v.y;
            return *this;
        }
        VecTor2& operator*=(const VecTor2& v) {
            x *= v.x;
            y *= v.y;
            return *this;
        }
        VecTor2& operator/=(const VecTor2& v) {
            x /= v.x;
            y /= v.y;
            return *this;
        }
    };
    struct VecTor3 {
        float x;
        float y;
        float z;
        VecTor3() {
            this->x = 0;
            this->y = 0;
            this->z = 0;
        }
        VecTor3(float x, float y, float z) {
            this->x = x;
            this->y = y;
            this->z = z;
        }
        bool operator!=(const VecTor3& Pos) {
            if (this->x != Pos.x || this->y != Pos.y || this->z != Pos.z) {
                return true;
            }
            return false;
        }
        VecTor3 operator+(float v) const {
            return VecTor3(x + v, y + v, z + v);
        }
        VecTor3 operator-(float v) const {
            return VecTor3(x - v, y - v, z - v);
        }
        VecTor3 operator*(float v) const {
            return VecTor3(x * v, y * v, z * v);
        }
        VecTor3 operator/(float v) const {
            return VecTor3(x / v, y / v, z / v);
        }
        VecTor3& operator+=(float v) {
            x += v;
            y += v;
            z += v;
            return *this;
        }
        VecTor3& operator-=(float v) {
            x -= v;
            y -= v;
            z -= v;
            return *this;
        }
        VecTor3& operator*=(float v) {
            x *= v;
            y *= v;
            z *= v;
            return *this;
        }
        VecTor3& operator/=(float v) {
            x /= v;
            y /= v;
            z /= v;
            return *this;
        }
        VecTor3 operator+(const VecTor3& v) const {
            return VecTor3(x + v.x, y + v.y, z + v.z);
        }
        VecTor3 operator-(const VecTor3& v) const {
            return VecTor3(x - v.x, y - v.y, z - v.z);
        }
        VecTor3 operator*(const VecTor3& v) const {
            return VecTor3(x * v.x, y * v.y, z * v.z);
        }
        VecTor3 operator/(const VecTor3& v) const {
            return VecTor3(x / v.x, y / v.y, z / v.z);
        }
        VecTor3& operator+=(const VecTor3& v) {
            x += v.x;
            y += v.y;
            z += v.z;
            return *this;
        }
        VecTor3& operator-=(const VecTor3& v) {
            x -= v.x;
            y -= v.y;
            z -= v.z;
            return *this;
        }
        VecTor3& operator*=(const VecTor3& v) {
            x *= v.x;
            y *= v.y;
            z *= v.z;
            return *this;
        }
        VecTor3& operator/=(const VecTor3& v) {
            x /= v.x;
            y /= v.y;
            z /= v.z;
            return *this;
        }
    };

    struct VecTor4 {
        float x;
        float y;
        float z;
        float w;
        VecTor4() {
            this->x = 0;
            this->y = 0;
            this->z = 0;
            this->w = 0;
        }
        VecTor4(float x, float y, float z, float w) {
            this->x = x;
            this->y = y;
            this->z = z;
            this->w = w;
        }
        bool operator!=(const VecTor4& Pos) {
            if (this->x != Pos.x || this->y != Pos.y || this->z != Pos.z || this->w != Pos.w) {
                return true;
            }
            return false;
        }
        VecTor4 operator+(float v) const {
            return VecTor4(x + v, y + v, z + v, w + v);
        }
        VecTor4 operator-(float v) const {
            return VecTor4(x - v, y - v, z - v, w - v);
        }
        VecTor4 operator*(float v) const {
            return VecTor4(x * v, y * v, z * v, w * v);
        }
        VecTor4 operator/(float v) const {
            return VecTor4(x / v, y / v, z / v, w / v);
        }
        VecTor4& operator+=(float v) {
            x += v;
            y += v;
            z += v;
            w += v;
            return *this;
        }
        VecTor4& operator-=(float v) {
            x -= v;
            y -= v;
            z -= v;
            w -= v;
            return *this;
        }
        VecTor4& operator*=(float v) {
            x *= v;
            y *= v;
            z *= v;
            w *= v;
            return *this;
        }
        VecTor4& operator/=(float v) {
            x /= v;
            y /= v;
            z /= v;
            w /= v;
            return *this;
        }
        VecTor4 operator+(const VecTor4& v) const {
            return VecTor4(x + v.x, y + v.y, z + v.z, w + v.w);
        }
        VecTor4 operator-(const VecTor4& v) const {
            return VecTor4(x - v.x, y - v.y, z - v.z, w - v.w);
        }
        VecTor4 operator*(const VecTor4& v) const {
            return VecTor4(x * v.x, y * v.y, z * v.z, w * v.w);
        }
        VecTor4 operator/(const VecTor4& v) const {
            return VecTor4(x / v.x, y / v.y, z / v.z, w / v.w);
        }
        VecTor4& operator+=(const VecTor4& v) {
            x += v.x;
            y += v.y;
            z += v.z;
            w += v.w;
            return *this;
        }
        VecTor4& operator-=(const VecTor4& v) {
            x -= v.x;
            y -= v.y;
            z -= v.z;
            w -= v.w;
            return *this;
        }
        VecTor4& operator*=(const VecTor4& v) {
            x *= v.x;
            y *= v.y;
            z *= v.z;
            w *= v.w;
            return *this;
        }
        VecTor4& operator/=(const VecTor4& v) {
            x /= v.x;
            y /= v.y;
            z /= v.z;
            w /= v.w;
            return *this;
        }
    };

    颜色 颜色;  // 颜色的类
    float PX, PY;
    float MIDDLE, BOTTOM, TOP;
    float left, right, top, top1, bottom;
    bool isAiming;
    void 初始化绘图(int X, int Y);
    void 初始化坐标(D4DVector& 屏幕坐标, 骨骼数据& 骨骼);
    void 绘制方框(bool isboot);
    void 绘制人数(int 人机, int 真人);
    void 绘制全图人数();
    void 绘制距离(int 距离, int 队伍);
    void 绘制射线(骨骼数据& 骨骼);
    void 绘制血量(float 最大血量, float 当前血量, bool isbot, bool isBoss, bool isElite);
    void 绘制名字(string 名字, bool isboot, float 计时, bool 是否掐雷, char* 类名, int 阵营, int Bonecount);
    void 绘制骨骼(骨骼数据& 骨骼, D4DVector& 屏幕坐标, bool LineOfSightTo, float 距离);
    void 绘制手持(int 手持, int 状态, int 子弹, int 最大子弹);
    void 绘制车辆(D4DVector 屏幕坐标, int 距离, int CarrierID);
    void 绘制自瞄触摸范围(float 触摸范围, float 触摸范围X, float 触摸范围Y);
    void 绘制开火范围(float 开火范围, float 开火范围X, float 开火范围Y);
    void 绘制连点范围(float 连点范围, float 连点范围X, float 连点范围Y);
    void 绘制开镜范围(float 开镜范围, float 开镜范围X, float 开镜范围Y);
    void 绘制加粗文本(float size, float x, float y, ImColor color, ImColor color1, const char* str);
    void 绘制字体描边(float size, int x, int y, ImVec4 color, const char* str);
    void RenderRadarScan(ImDrawList* draw_list, ImVec2 center, float radius, int numSegments, float& rotationAngle, float lineLength);
    void 绘制瞄准信息();

    bool WorldTurnScreen(VecTor2& Screen, VecTor3 World, float Matrix[]);

    void ExplosionRange(D3DVector Obj, ImColor color, float Range, float thickn, float Matrix[]);

    void Parabola(VecTor3 obj, float Matrix[]);
};
// 绘制
class 绘制 {
    struct ColorTable {
        float 方框颜色[4] = {0.0, 1.0, 0.0, 1.0};
        float 射线颜色[4] = {0.0, 1.0, 0.0, 1.0};
        float 骨骼颜色[4] = {1.0, 1.0, 1.0, 1.0};
        float 血量颜色[4] = {1.0, 0.0, 0.0, 1.0};
        float 阵营颜色[4] = {1.0, 1.0, 0.0, 1.0};
        float 距离颜色[4] = {1.0, 1.0, 1.0, 1.0};
        float 名称颜色[4] = {1.0, 1.0, 1.0, 1.0};
    };
    struct map_node {
        long int start_addr;    // 起始地址
        long int end_addr;      // 结束地址
        struct map_node* next;  // 下一个节点
    };

    struct 压枪 {
        float m416;
        float scar_l;
        float aug;
        float famas;
        float g36c;
        float m249;
        float 喷子;
        float akm;
        float m762;
        float 蜜獾;
        float pkm;
        float mg3;
        float mg_36;
        float p90;
        float uzi;
        float ump45;
        float vector;
        float 汤姆逊;
        float 野牛;
        float mini14;
        float sks;
        float m417;
        float mk20_h;
        float mk12;
        float vss;
        float 扫车 = 1.2f;
    };

   public:
    float mk20, m417 = 1.0f;
    float 轻型压枪力度 = 1.f;
    float 拇指压枪力度 = 0.35f;
    float 垂直压枪力度 = 0.43f;
    float 直角压枪力度 = 0.50f;
    int 世界数量;
    float 骨骼距离限制 = 300;
    int 防录屏, 自瞄模式, 无后台开关, 控制延迟;
    char 卡密[250];

    bool Winorlose = false;
    ImVec2 Pos;
    int winWidth = 684;
    int winHeith = 896;

    bool 是否开启自瞄页面 = false;

    压枪 压枪力;
    压枪 预判度;

    float 握把[100];

    uintptr_t 解密数组;
    long int 解密模式 = 0x4000;

    int 被瞄准对象数量 = 0;

    FILE* numSave = nullptr;

    char 悬浮窗标题[200];

    ColorTable Colorset[2];  // 颜色配置
    int pid;
    bool 线程 = 0;  // 用于判断更新数据显示是否加载
    float PX, PY;   // 绘制用的分辨率
    float 真实PX, 真实PY;
    bool Validate;
    Kernel 读写;  // 创建读写结构体

    float 物资颜色[4] = {0.0, 1.0, 0.0, 1.0};
    float 手持颜色[4] = {0.0, 1.0, 0.0, 1.0};
    float 车辆颜色[4] = {0.0, 1.0, 0.0, 1.0};

    StringFloatMap 手雷类;
    Timer 计时器;  // 计时器

    bool Shelter[14];

    

    地址 地址;
    开关 按钮;
    计算 计算;
    骨骼* 骨骼;
    绘图 绘图;
    自瞄 自瞄;
    备份 备份;
    std::mutex mtx;
    自瞄信息 自瞄函数[100];
    瞄准信息 被瞄信息[100];
    自身数据 自身数据;  // 创建自身数据结构体
    对象地址 对象地址;  // 创建对象地址结构体
    对象信息 对象信息;  // 创建敌人信息结构体数组
    void 初始化绘制(string 包名, int 真实X, int 真实Y);
    void 自瞄主线程();
    void 陀螺仪自瞄主线程();
    void 陀螺仪自瞄主线程2();
    void 停火闪镜线程();
    int findminat(float 自瞄范围);
    float 陀螺仪灵敏度补偿(float Fov);
    void 更新地址数据();
    void 多线程更新地址();
    void 更新对象地址();
    void 更新对象数据();
    void 绘制载具信息();
    void 运行绘制();
    ImColor floatArrToImColor(float arr[4]);
    void hide_process();
    string getBoxName(int id);
    void OffScreen(ImDrawList* ImDraw, D4DVector Obj, float camear, ImU32 color, float Radius, float 距离);
    void GetTouch();
    void 保存配置();
    void 读取配置();
    bool 自瞄触发(float 距离, bool isPZ);
    const char* getMaterialName(char* name);
    int Cloudcheck();
    const char* Level(char* name);
    int Acquisitionsite();
    D3DVector Missedtyping();
    void 掩体线程();
    void InitShoot();

    void InitMaps();
    void print_maps(struct map_node* head);
    void free_maps(struct map_node* head);
    void readmaps_v(struct map_node** head);
};

class 布局 {
   public:
    // 布局UI
    void 开启悬浮窗();
    void 绘制悬浮窗();
    int 初始化程序();
};

#endif
