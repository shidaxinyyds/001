#ifndef DRIVER_H
#define DRIVER_H
#include <unistd.h>
#include <cstring>
#if __cplusplus < 201402L
#warning "C++版本过低，推荐使用14、17、20、23，设置示例：-std=c++14"
#endif
// 驱动接口类，支持内核版本4.9~6.12
class Driver
{
  public:
    // 类ID，记录类起始地址，用于判断初始化是否成功，防止空指针异常/段错误，gid != 0 则表示初始化成功
	uint64_t gid;
	
	// 类ID,记录类结束地址,作用同上
	uint64_t uid;
	
    // 构造方法
	Driver() : gid(0), uid(0)
	{
		// 由于开发阶段需要可变字符，若编译出现报错，使用 -w 参数即可
		initkey("e2c53d16e1af2c7e72941cc6a7eae1d726d54bcf32a5e3f94405f498f0f18ea9704a2f3b2c");
	}
	
    // 构析方法
    ~Driver();
            
	// 版本ID(密钥)，防止版本接口不一致导致内核崩溃，切勿随意更换，不需要手动调用
	void initkey(char *key);		    		
	
	// CPU亲和设置，cpuset(4)代表运行在CPU4上，cpuset(4,6)代表指定范围随机运行在CPU4-CPU6
	static void cpuset(uint8_t start, uint8_t end = 0);
    
    // 初始化pid并且同步帧率，默认90帧，最高支持240帧，创建一个读写对象务必要初始一次
	void initpid(pid_t pid, uint8_t fps = 90);	
	
	// 获取进程pid，传入进程(包名)，从内核层安全获取pid，当遇到加密混淆进程时可传入comm字段，参考/proc/pid/comm
	pid_t get_pid(const char *name, const char *comm = nullptr);
			
	// 获取模块地址，传入pid、接收指针、模块名、模块(长度)大小，当size=0时默认取模块第一行，从内核层安全获取模块地址，支持多线程
	bool get_module_base(pid_t pid, void *buffer, const char *name, size_t size = 0);
	
	// 获取地址内存段，传入pid、接收指针(接收16字节)、目标地址、内存段权限、模块名、是否模糊(当前内存段不匹配则尽可能返回下一段匹配的内存段)，从内核层安全获取内存段，支持多线程
	bool get_module_base(pid_t pid, void *buffer, uintptr_t addr, const char *prot, const char *name, bool fuzzy);
	
	// 硬件级读取数据，直接读硬件地址(直接内存访问)，传入目标地址、类型大小，指数级安全，由于不使用CPU，效率相应降低，该方法一般用于访问常驻、大块的内存，正确使用则效率最高，支持单进程多线程，支持 >= 5.4.x
	void *read_safe(uintptr_t addr, size_t size);
	
	// 无CPU缓存的第三方自建缓存内核读取写法，传入地址、接收指针、类型大小，不使用CPU缓存，使用第三方缓存，效率中等，支持单进程多线程，支持 >= 5.4.x
	bool read_nocache(uintptr_t addr, void *buffer, size_t size);

    // 内核层读取数据，使用记录法，读前记录CPU缓存，读后恢复读前CPU缓存状态，只读已映射到内核空间的地址，传入地址、接收指针、类型大小，支持单进程多线程，效率较高, 支持 >= 4.9.x
	bool read(uintptr_t addr, void *buffer, size_t size);
	
	// 内核层修改数据，只写已映射到内核空间的地址，传入地址、数据指针、类型大小，支持多线程
	bool write(uintptr_t addr, void *buffer, size_t size);

	// 模板方法，传入地址，返回地址上的值，支持多线程，该方法仅适用于8字节以下的基本类型
	template <typename T> T read_safe(uintptr_t addr)
	{
		// 使用简洁的c风格强转
		return *(T*)this->read_safe(addr, sizeof(T));
	}		
	
	// 模板方法，传入地址，返回地址上的值，支持多线程
	template <typename T> T read_nocache(uintptr_t addr)
	{
		T res{};
		if (this->read_nocache(addr, &res, sizeof(T)))
			return res;
		return 0;
	}
		
	// 模板方法，传入地址，返回地址上的(数据)值，支持多线程
	template <typename T> T read(uintptr_t addr)
	{
		T res{};
		if (this->read(addr, &res, sizeof(T)))
			return res;
		return 0;
	}
	
	// 模板方法，传入地址，修改后的(数据)值，支持多线程
	template <typename T> bool write(uintptr_t addr, T value)
	{
		return this->write(addr, &value, sizeof(T));
	}
	
	// 获取模块方法的封装 传入pid、模块名称，返回模块起始地址
	uintptr_t get_module_base(pid_t pid, char *name)
	{
	    struct
	    {
	        uintptr_t start;
	        uintptr_t end;
	    } addr{};
	    get_module_base(pid, &addr, name);
	    return addr.start;
	}
	
	// 获取模块方法的封装 传入pid、模块名称、模块长度，返回模块起始地址
	uintptr_t get_module_base(pid_t pid, char *name, size_t size)
	{
	    struct
	    {
	        uintptr_t start;
	        uintptr_t end;
	    } addr{};
	    get_module_base(pid, &addr, name, size);
	    return addr.start;
	}
	    
	// 解锁并释放资源 同一时间只能有一个用户在操作驱动(单进程多线程，最大支持 nproc/2 个线程) 进程结束后delete或者主动调用destroy释放资源，释放驱动,否则下次运行可能失败
	void destroy();
	
	// read 读取的最大字节长度 16384字节 具体看设备情况
	size_t read_max_size;
	
	// read_nocache 读取的最大长度 3072字节
    size_t read_nocache_max_size;
    
    // read_safe 读取的最大长度 4096
    size_t read_safe_max_size;
	
};

// 这是扩展类
class Driver_EXT : public Driver
{    
  public:
    
    bool read_ext(uintptr_t addr, void *buffer, size_t size) noexcept
    {        
        uint8_t *src = (uint8_t *)buffer;
        while (size > read_max_size)
        {
            if (!read(addr, src, read_max_size))
                return false;
            addr += read_max_size;
            src  += read_max_size;
            size -= read_max_size;            
        }
        if (size)
            return read(addr, src, size);
        return true;
    }
    
    // 扩展后支持读取[1,+∞]字节
    bool read_nocache_ext(uintptr_t addr, void *buffer, size_t size) noexcept
    {        
        uint8_t *src = (uint8_t *)buffer;
        while (size > read_nocache_max_size)
        {
            if (!read_nocache(addr, src, read_nocache_max_size))
                return false;
            addr += read_nocache_max_size;
            src  += read_nocache_max_size;
            size -= read_nocache_max_size;            
        }
        if (size)
            return read_nocache(addr, src, size);
        return true;
    }
    
    bool read_safe_ext(uintptr_t addr, void *buffer, size_t size) noexcept
    {        
        char *dst = (char*)buffer;
        while (size > read_safe_max_size)
        {
            memcpy(dst, read_safe(addr, read_safe_max_size), read_safe_max_size);
            addr += read_safe_max_size;
            size -= read_safe_max_size;
            dst  += read_safe_max_size;
        }
        if (size)
            memcpy(dst, read_safe(addr, size), size);
        return true;
    }
    
};

#endif
