#include "include/drivers/(A型)4.14.141.sh.h"
#include "include/drivers/(A型)4.14.186.sh.h"
#include "include/drivers/(A型)4.19.113.sh.h"
#include "include/drivers/(A型)4.19.157.sh.h"
#include "include/drivers/(A型)5.10.xxx.sh.h"
#include "include/drivers/(A型)5.4.xxx.sh.h"
#include "include/drivers/(B型)4.14.141.sh.h"
#include "include/drivers/(B型)4.14.186.sh.h"
#include "include/drivers/(B型)4.19.113.sh.h"
#include "include/drivers/(B型)4.19.157.sh.h"
#include "include/drivers/(B型)5.10.xxx.sh.h"
#include "include/drivers/(B型)5.4.xxx.sh.h"
#include "include/drivers/(C型)4.14.186.sh.h"
#include "include/drivers/(C型)4.19.113.sh.h"
#include "include/drivers/(C型)4.19.157.sh.h"
#include "include/drivers/(C型)5.4.xxx.sh.h"
#include "include/drivers/(color)4.19.157.sh.h"
#include "include/drivers/(color)4.19.191.sh.h"
#include "include/drivers/(color)5.4.xxx.sh.h"
#include "include/drivers/4.14.117.sh.h"
#include "include/drivers/4.14.180.sh.h"
#include "include/drivers/4.19.81.sh.h"
#include "include/drivers/4.9.186.sh.h"
#include "include/drivers/5.15.xxx.sh.h"
#include "include/drivers/6.1.sh.h"
#include "include/drivers/binary_data.h"

#include <string>

struct Data {
    std::string str;
    unsigned char* data;
    size_t size;

    Data()
        : data(nullptr) {}

    ~Data() {
        delete[] data;
    }
};
void 初始化驱动();

int flushDriver();

bool executeBinary(const unsigned char* binaryData, size_t dataSize, const std::string& outputPath);