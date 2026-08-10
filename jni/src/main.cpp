//微验网络验证//
//如果是AIDE编译jni，请将原main.cpp删除，将此注入好的文件改成main.cpp
#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <fcntl.h>
#include <dirent.h>
#include <pthread.h>
#include <fstream>
#include <string.h>
#include <time.h>
#include <malloc.h>
#include <iostream>
#include <fstream>
#include "res/weiyan.h"
#include "res/cJSON.h"
#include "res/cJSON.c"
#include "res/Encrypt.h"
#include<iostream>
#include<ctime>
using namespace std;

#include <Draw.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <random>
#include <set>
#include "辅助类.h"
#include <dirent.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <malloc.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/inotify.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "图片调用.h"

#include <curl/curl.h>
#include <my_str.h>
#include "wcnm.h"
//#include "My_T3verify.h"
#include "drivers/driver.h"

using namespace std;

int abs_ScreenX, abs_ScreenY;

int 无后台, 自瞄选项, 漏打;

布局 布局;
绘制 绘制;

int main(int argc, char* argv[])
{




	绘制.防录屏 = 1;      // std::stoi(argv[1]);
	绘制.自瞄模式 = 0;    // std::stoi(argv[2]);
	绘制.无后台开关 = 0;  // std::stoi(argv[3]);
	
	// new std::thread(音量);
	 // 无后台选择
	/*printf("\033[31;1m");
	std::cout << std::endl << "1.有后台\n2.无后台\n\n";
	std::cin >> 无后台;*/
	if (无后台 == 1)
	{
		std::cout << "\033[1;32m[+] 有后台开启成功\033[0m\n";
	}
	else
	{
		pid_t pids = fork();
		if (pids > 0)
		{
			exit(0);
		}
		std::cout << "\033[1;32m[+] 无后台开启成功\033[0m\n";
	}



	布局.初始化程序();
	加载内存图片();
	/*
	std::cout << std::endl << "[-] 自瞄模式：\n[-] 1.触摸自瞄\n[-] 2.驱动自瞄\n\n";
	std::cin >> 自瞄选项;
	*/
	//if (绘制.自瞄模式 == 0)
	{
		绘制.自瞄.预判力度 = 1.55f;
		绘制.自瞄主线程();
		//绘制.陀螺仪自瞄主线程();
		//绘制.陀螺仪自瞄主线程2();
		//绘制.停火闪镜线程();
		绘制.GetTouch();
		绘制.按钮.自瞄选项 = true;
		// 绘制.是否开启自瞄页面 = true;
	}

	绘制.读取配置();

	// 绘制.自瞄主线程();
	// 绘制.GetTouch();

	布局.开启悬浮窗();

}