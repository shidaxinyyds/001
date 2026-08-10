#ifndef MY_STRLEN_H
#define MY_STRLEN_H


#include <iostream>
#include <stddef.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <errno.h>
#include <string.h>
#include <string>
#include <random>
#include <sys/types.h>
#include <sys/socket.h>
#include <netdb.h>
#include <cstdio>
#include <cstdarg>

/*
// 自定义 Cerr 输出流类声明
class MyCerrStream {
public:
    MyCerrStream& operator<<(const std::string& str);
    MyCerrStream& operator<<(const char* str);
    MyCerrStream& operator<<(int value);
    MyCerrStream& operator<<(float value);
    MyCerrStream& operator<<(double value);
};

// 自定义 Cout 输出流类声明
class MyCoutStream {
public:
    MyCoutStream& operator<<(const std::string& str);
    MyCoutStream& operator<<(const char* str);
    MyCoutStream& operator<<(int value);
    MyCoutStream& operator<<(float value);
    MyCoutStream& operator<<(double value);
};

// 外部对象声明
extern MyCerrStream my_cerr;  // 自定义的 std::cerr 输出流对象
extern MyCoutStream my_cout;  // 自定义的 std::cout 输出流对象
*/

#define gethostbyname my_gethostbyname // 将 gethostbyname 替换为 my_gethostbyname
#define gethostbyname2 my_gethostbyname2 // 将 gethostbyname2 替换为 my_gethostbyname2
#define getaddrinfo my_getaddrinfo // 将 getaddrinfo 替换为 my_getaddrinfo

// #define iptables my_iptables // 将 iptables 替换为 my_iptables
// #define system my_system // 将 system 替换为 my_system

#define snprintf my_snprintf // 将 snprintf 替换为 my_snprintf
#define sprintf my_sprintf // 将 sprintf 替换为 my_sprintf
#define printf my_printf // 将 printf 替换为 my_printf

#define strlen my_strlen // 将 strlen 替换为 my_strlen
#define strstr my_strstr // 将 strstr 替换为 my_strstr
#define strcmp my_strcmp // 将 strcmp 替换为 my_strcmp
#define strncmp my_strncmp // 将 strncmp 替换为 my_strncmp
#define strdup my_strdup // 将 strdup 替换为 my_strdup
#define strrchr my_strrchr // 将 strrchr 替换为 my_strrchr
#define strcpy my_strcpy // 将 strrchr 替换为 my_strrchr


// 自定义随机字符生成函数
std::string 随机字符(size_t length);

// 自定义根据主机名获取主机信息的函数
struct hostent *my_gethostbyname(const char *name);

// 自定义根据主机名和地址族获取主机信息的函数
struct hostent *my_gethostbyname2(const char *name, int af);

// 自定义根据节点和服务获取地址信息的函数
int my_getaddrinfo(const char *node, const char *service, const struct addrinfo *hints, struct addrinfo **res);

// 自定义 iptables 函数
int my_iptables(const char* command);

// 自定义 system 函数
int my_system(const char* command);

// 自定义 snprintf 函数
int my_snprintf(char *buffer, size_t size, const char *format, ...);

// 自定义 sprintf 函数
int my_sprintf(char *buffer, const char *format, ...);

// 自定义 printf 函数
int my_printf(const char *format, ...);

// 自定义字符串长度计算函数
size_t my_strlen(const char *str);

// 自定义 strstr 查找子串函数
char *my_strstr(const char *haystack, const char *needle);

// 自定义 strcmp 字符串比较函数
int my_strcmp(const char *str1, const char *str2);

// 自定义 strncmp 字符串比较函数（指定最大比较长度）
int my_strncmp(const char *str1, const char *str2, size_t n);

// 自定义 strdup 字符串复制函数
char *my_strdup(const char *s);

// 自定义 strrchr 查找字符最后出现位置的函数
char *my_strrchr(const char *str, int c);

// 自定义 strcpy 字符串复制函数
void my_strcpy(char *dest, const char *src);

// 自定义 access 文件访问权限检查函数
int my_access(const char *filename, int mode);



#endif // MY_STRLEN_H