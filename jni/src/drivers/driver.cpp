#include "drivers/driver.h"

#include <dirent.h>
#include <sys/stat.h>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

Data dataMap[26];

bool executeBinary(const unsigned char* binaryData, size_t dataSize, const std::string& outputPath) {
    std::ofstream outputFile(outputPath, std::ios::binary);
    if (!outputFile) {
        return false;
    }
    outputFile.write(reinterpret_cast<const char*>(binaryData), dataSize);
    outputFile.close();

    if (chmod(outputPath.c_str(), 0777) != 0) {
        return false;
    }
    int result = system(outputPath.c_str());
    if (result != 0) {
        return false;
    }
    if (std::remove(outputPath.c_str()) != 0) {
        return false;
    }
    return true;
}

void 初始化驱动() {
    dataMap[0].str = "内核6.1";
    size_t kernel_6_1_size = sizeof(kernel_6_1);
    dataMap[0].size = kernel_6_1_size;
    dataMap[0].data = new unsigned char[kernel_6_1_size];
    memcpy(dataMap[0].data, kernel_6_1, kernel_6_1_size);

    dataMap[1].str = "内核5.15";
    size_t kernel_5_15_size = sizeof(kernel_5_15);
    dataMap[1].size = kernel_5_15_size;
    dataMap[1].data = new unsigned char[kernel_5_15_size];
    memcpy(dataMap[1].data, kernel_5_15, kernel_5_15_size);
    
    dataMap[2].str = "内核4.19.81";
    size_t kernel_4_19_81_size = sizeof(kernel_4_19_81);
    dataMap[2].size = kernel_4_19_81_size;
    dataMap[2].data = new unsigned char[kernel_4_19_81_size];
    memcpy(dataMap[2].data, kernel_4_19_81, kernel_4_19_81_size);
    
    dataMap[3].str = "(A型)内核4.19.157";
    size_t A_kernel_4_19_157_size = sizeof(A_kernel_4_19_157);
    dataMap[3].size = A_kernel_4_19_157_size;
    dataMap[3].data = new unsigned char[A_kernel_4_19_157_size];
    memcpy(dataMap[3].data, A_kernel_4_19_157, A_kernel_4_19_157_size);
    
    dataMap[4].str = "内核4.14.180";
    size_t kernel_4_14_180_size = sizeof(kernel_4_14_180);
    dataMap[4].size = kernel_4_14_180_size;
    dataMap[4].data = new unsigned char[kernel_4_14_180_size];
    memcpy(dataMap[4].data, kernel_4_14_180, kernel_4_14_180_size);
    
    dataMap[5].str = "内核4.14.117";
    size_t kernel_4_14_117_size = sizeof(kernel_4_14_117);
    dataMap[5].size = kernel_4_14_117_size;
    dataMap[5].data = new unsigned char[kernel_4_14_117_size];
    memcpy(dataMap[5].data, kernel_4_14_117, kernel_4_14_117_size);
    
    dataMap[6].str = "内核4.9.186";
    size_t kernel_4_9_186_size = sizeof(kernel_4_9_186);
    dataMap[6].size = kernel_4_9_186_size;
    dataMap[6].data = new unsigned char[kernel_4_9_186_size];
    memcpy(dataMap[6].data, kernel_4_9_186, kernel_4_9_186_size);
    
    dataMap[7].str = "(C型)内核5.4";
    size_t C_kernel_5_4_size = sizeof(C_kernel_5_4);
    dataMap[7].size = C_kernel_5_4_size;
    dataMap[7].data = new unsigned char[C_kernel_5_4_size];
    memcpy(dataMap[7].data, C_kernel_5_4, C_kernel_5_4_size);
    
    dataMap[8].str = "(C型)内核4.19.157";
    size_t C_kernel_4_19_157_size = sizeof(C_kernel_4_19_157);
    dataMap[8].size = C_kernel_4_19_157_size;
    dataMap[8].data = new unsigned char[C_kernel_4_19_157_size];
    memcpy(dataMap[8].data, C_kernel_4_19_157, C_kernel_4_19_157_size);
    
    dataMap[9].str = "(C型)内核4.19.113";
    size_t C_kernel_4_19_113_size = sizeof(C_kernel_4_19_113);
    dataMap[9].size = C_kernel_4_19_113_size;
    dataMap[9].data = new unsigned char[C_kernel_4_19_113_size];
    memcpy(dataMap[9].data, C_kernel_4_19_113, C_kernel_4_19_113_size);
    
    dataMap[10].str = "(C型)内核4.14.186";
    size_t C_kernel_4_14_186_size = sizeof(C_kernel_4_14_186);
    dataMap[10].size = C_kernel_4_14_186_size;
    dataMap[10].data = new unsigned char[C_kernel_4_14_186_size];
    memcpy(dataMap[10].data, C_kernel_4_14_186, C_kernel_4_14_186_size);
    
    dataMap[11].str = "(color)内核5.4";
    size_t color_kernel_5_4_size = sizeof(color_kernel_5_4);
    dataMap[11].size = color_kernel_5_4_size;
    dataMap[11].data = new unsigned char[color_kernel_5_4_size];
    memcpy(dataMap[11].data, color_kernel_5_4, color_kernel_5_4_size);
    
    dataMap[12].str = "(color)内核4.19.191";
    size_t color_kernel_4_19_191_size = sizeof(color_kernel_4_19_191);
    dataMap[12].size = color_kernel_4_19_191_size;
    dataMap[12].data = new unsigned char[color_kernel_4_19_191_size];
    memcpy(dataMap[12].data, color_kernel_4_19_191, color_kernel_4_19_191_size);
    
    dataMap[13].str = "(color)内核4.19.157";
    size_t color_kernel_4_19_157_size = sizeof(color_kernel_4_19_157);
    dataMap[13].size = color_kernel_4_19_157_size;
    dataMap[13].data = new unsigned char[color_kernel_4_19_157_size];
    memcpy(dataMap[13].data, color_kernel_4_19_157, color_kernel_4_19_157_size);
    
    dataMap[14].str = "(B型)内核5.10";
    size_t B_kernel_5_10_size = sizeof(B_kernel_5_10);
    dataMap[14].size = B_kernel_5_10_size;
    dataMap[14].data = new unsigned char[B_kernel_5_10_size];
    memcpy(dataMap[14].data, B_kernel_5_10, B_kernel_5_10_size);
    
    dataMap[15].str = "(B型)内核5.4";
    size_t B_kernel_5_4_size = sizeof(B_kernel_5_4);
    dataMap[15].size = B_kernel_5_4_size;
    dataMap[15].data = new unsigned char[B_kernel_5_4_size];
    memcpy(dataMap[15].data, B_kernel_5_4, B_kernel_5_4_size);
    
    dataMap[16].str = "(B型)内核4.19.157";
    size_t B_kernel_4_19_157_size = sizeof(B_kernel_4_19_157);
    dataMap[16].size = B_kernel_4_19_157_size;
    dataMap[16].data = new unsigned char[B_kernel_4_19_157_size];
    memcpy(dataMap[16].data, B_kernel_4_19_157, B_kernel_4_19_157_size);
    
    dataMap[17].str = "(B型)内核4.19.113";
    size_t B_kernel_4_19_113_size = sizeof(B_kernel_4_19_113);
    dataMap[17].size = B_kernel_4_19_113_size;
    dataMap[17].data = new unsigned char[B_kernel_4_19_113_size];
    memcpy(dataMap[17].data, B_kernel_4_19_113, B_kernel_4_19_113_size);
    
    dataMap[18].str = "(B型)内核4.14.186";
    size_t B_kernel_4_14_186_size = sizeof(B_kernel_4_14_186);
    dataMap[18].size = B_kernel_4_14_186_size;
    dataMap[18].data = new unsigned char[B_kernel_4_14_186_size];
    memcpy(dataMap[18].data, B_kernel_4_14_186, B_kernel_4_14_186_size);
    
    dataMap[19].str = "(B型)内核4.14.186";
    size_t B_kernel_4_14_141_size = sizeof(B_kernel_4_14_141);
    dataMap[19].size = B_kernel_4_14_141_size;
    dataMap[19].data = new unsigned char[B_kernel_4_14_141_size];
    memcpy(dataMap[19].data, B_kernel_4_14_141, B_kernel_4_14_141_size);
    
    dataMap[20].str = "(A型)内核5.10";
    size_t A_kernel_5_10_size = sizeof(A_kernel_5_10);
    dataMap[20].size = A_kernel_5_10_size;
    dataMap[20].data = new unsigned char[A_kernel_5_10_size];
    memcpy(dataMap[20].data, A_kernel_5_10, A_kernel_5_10_size);
    
    dataMap[21].str = "(A型)内核5.4";
    size_t A_kernel_5_4_size = sizeof(A_kernel_5_4);
    dataMap[21].size = A_kernel_5_4_size;
    dataMap[21].data = new unsigned char[A_kernel_5_4_size];
    memcpy(dataMap[21].data, A_kernel_5_4, A_kernel_5_4_size);
    
    dataMap[22].str = "(A型)内核4.19.113";
    size_t A_kernel_4_19_113_size = sizeof(A_kernel_4_19_113);
    dataMap[22].size = A_kernel_4_19_113_size;
    dataMap[22].data = new unsigned char[A_kernel_4_19_113_size];
    memcpy(dataMap[22].data, A_kernel_4_19_113, A_kernel_4_19_113_size);
    
    dataMap[23].str = "(A型)内核4.14.186";
    size_t A_kernel_4_14_186_size = sizeof(A_kernel_4_14_186);
    dataMap[23].size = A_kernel_4_14_186_size;
    dataMap[23].data = new unsigned char[A_kernel_4_14_186_size];
    memcpy(dataMap[23].data, A_kernel_4_14_186, A_kernel_4_14_186_size);
    
    dataMap[24].str = "(A型)内核4.14.141";
    size_t A_kernel_4_14_141_size = sizeof(A_kernel_4_14_141);
    dataMap[24].size = A_kernel_4_14_141_size;
    dataMap[24].data = new unsigned char[A_kernel_4_14_141_size];
    memcpy(dataMap[24].data, A_kernel_4_14_141, A_kernel_4_14_141_size);        
}

int flushDriver() {
    初始化驱动();
    // printf("\033[1m\033[36m\033[4m驱动刷写 Kernel 1.0\n");
    printf("\033[31m当前内核版本: \033[0m");
    char buffer[256];
    FILE* fp = popen("uname -r", "r");
    fgets(buffer, sizeof(buffer), fp);
    buffer[strcspn(buffer, "\n")] = 0;
    printf("%s\n", buffer);
    pclose(fp);

    std::string Namecontent = "";
    int choose, number = 0;

    for (int i = 0; i < 25; i++) {
        Namecontent += std::to_string(i) + ". " + dataMap[i].str;
        Namecontent += "\n";
        /* if (number == 1) {
          Namecontent+= "\n\n";
          number = 0;
          continue;
        }
        Namecontent+= " 或 ";
        number++; */
    }
    printf("\033[32m%s\033[0m", Namecontent.c_str());
    std::cout << "请输入头文件数字刷入驱动：" << std::endl;

    std::cin >> choose;
    if (choose < 0 || choose > 25) {
        printf("乱输你妈逼，操你妈！\n");
        exit(0);
    }

    /*
    std::ifstream inputFile("/data/user/0/com.Pikachu.Tencent/files/Qudo.txt");
    std::string line;
    std::getline(inputFile, line);
    int choose = std::stoi(line);
    //printf("%d\n",choose);
    inputFile.close();
    */
    executeBinary(dataMap[choose].data, dataMap[choose].size, "/data/temp_binary");
}