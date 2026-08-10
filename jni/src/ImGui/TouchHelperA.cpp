#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>
#include <pthread.h>
#include <cmath>
#include <linux/input.h>
#include <linux/uinput.h>
#include <random>
#include "spinlock.h"
#include "TouchHelperA.h"
#include "src/辅助类.h"

#include "imgui.h"

#define maxE 5
#define maxF 10
#define UNGRAB 0
#define GRAB 1

int Touch_ID;
int Touch_I;
int Touch_I_bak;
int Touch_Global_SLOT;
int Touch_Temporary_SLOT;
int Touch_Temporary_SLOT_Bak;
Vector2 Touch_Clicks;

extern 绘制 绘制;

bool other_touch;

static uint32_t orientation = 0;
static float screenHeight = 0, screenWidth = 0;

struct touchObj {
  bool isDown = false;
  bool isTmpDown = false;
  int x = 0;
  int y = 0;
  int id = 0;
  int size1 = 0;
  int size2 = 0;
  int size3 = 0;
};
struct targ {
  int fdNum;
  float S2TX;
  float S2TY;
};
static struct {
  input_event downEvent[2]{{{}, EV_KEY, BTN_TOUCH,       1},
    {{}, EV_KEY, BTN_TOOL_FINGER, 1}};
  input_event event[512]{0};
} input;

static targ targF[maxE];

static touchObj Finger[maxE][maxF];

static int fdNum = 0, origfd[maxE], nowfd;

static float scale_x, scale_y;

static bool Touch_initialized = false;

static bool Touch_readOnly = false;


// 默认随机范围：1.5~2次/秒（对应间隔500ms~666ms）
static std::chrono::milliseconds minIntervalMs(500);
static std::chrono::milliseconds maxIntervalMs(666);
static std::mt19937 rng(std::random_device{}()); // 线程安全的随机数生成器


// 设置频率范围（转换为时间间隔）
void setClickFrequencyRange(float minClicksPerSec, float maxClicksPerSec)
{
    if (minClicksPerSec > 0 && maxClicksPerSec > minClicksPerSec)
    {
        minIntervalMs = std::chrono::milliseconds(static_cast<int>(1000 / maxClicksPerSec));
        maxIntervalMs = std::chrono::milliseconds(static_cast<int>(1000 / minClicksPerSec));
    }
}


// 生成随机间隔
std::chrono::milliseconds getRandomInterval()
{
    std::uniform_int_distribution<int> dist(
        minIntervalMs.count(),
        maxIntervalMs.count()
    );
    return std::chrono::milliseconds(dist(rng));
}


static bool checkDeviceIsTouch(int fd);
static void genRandomString(char *string, int length) {
  int flag, i;
  srand((unsigned) time(NULL) + length);
  for (i = 0; i < length - 1; i++) {
    flag = rand() % 3;
    switch (flag) {
      case 0:
      string[i] = 'A' + rand() % 26;
      break;
      case 1:
      string[i] = 'a' + rand() % 26;
      break;
      case 2:
      string[i] = '0' + rand() % 10;
      break;
      default:
      string[i] = 'x';
      break;
    }
  }
  string[length - 1] = '\0';
}

Vector2 Touch2Screen(const Vector2 &coord) {
  float x = coord.x, y = coord.y;
  float xt = x / scale_x;
  float yt = y / scale_y;
  
  if (other_touch) {
    switch (orientation) {
      case 1:
      x = xt;
      y = yt;
      break;
      case 2:
      y = yt;
      x = screenHeight - xt;
      break;
      case 3:
      x = screenHeight - xt;
      y = screenWidth - yt;
      break;
      default:
      y = xt;
      x = screenHeight - yt;
      break;
    }
  } else {
    switch (orientation) {
      case 1:
      x = yt;
      y = screenHeight - xt;
      break;
      case 2:
      x = screenHeight - xt;
      y = screenWidth - yt;
      break;
      case 3:
      y = xt;
      x = screenWidth - yt;
      break;
      default:
      x = xt;
      y = yt;
      break;
    }
  }
  return {x, y};
}

// 屏幕坐标转触摸坐标（Touch2Screen的逆运算）
Vector2 Screen2Touch(const Vector2& screenCoord)
{
    float x = screenCoord.x, y = screenCoord.y;
    float xt, yt;

    if (other_touch)
    {
        switch (orientation)
        {
        case 1:  // 原始：x = xt, y = yt
            xt = x;
            yt = y;
            break;
        case 2:  // 原始：y = yt, x = screenHeight - xt
            xt = screenHeight - x;
            yt = y;
            break;
        case 3:  // 原始：x = screenHeight - xt, y = screenWidth - yt
            xt = screenHeight - x;
            yt = screenWidth - y;
            break;
        default: // 原始：y = xt, x = screenHeight - yt
            xt = y;
            yt = screenHeight - x;
            break;
        }
    }
    else
    {
        switch (orientation)
        {
        case 1:  // 原始：x = yt, y = screenHeight - xt
            xt = screenHeight - y;
            yt = x;
            break;
        case 2:  // 原始：x = screenHeight - xt, y = screenWidth - yt
            xt = screenHeight - x;
            yt = screenWidth - y;
            break;
        case 3:  // 原始：y = xt, x = screenWidth - yt
            xt = y;
            yt = screenWidth - x;
            break;
        default: // 原始：x = xt, y = yt
            xt = x;
            yt = y;
            break;
        }
    }

    // 逆向缩放
    float orig_x = xt * scale_x;
    float orig_y = yt * scale_y;

    return { orig_x, orig_y };
}


static void Upload() {
  static bool bTouch = false;
  static bool isFirstDown = true;
  while (bTouch);
  bTouch = true;
  int tmpCnt = 0, tmpCnt2 = 0, i, j;
  for (i = 0; i < fdNum; i++) {
    for (j = 0; j < maxF; j++) {
      if (Finger[i][j].isDown) {
        if (tmpCnt2++ > 10) {
          goto finish;
        }
        input.event[tmpCnt].type = EV_ABS;
        input.event[tmpCnt].code = ABS_X;
        input.event[tmpCnt].value = Finger[i][j].x;
        tmpCnt++;
        
        input.event[tmpCnt].type = EV_ABS;
        input.event[tmpCnt].code = ABS_Y;
        input.event[tmpCnt].value = Finger[i][j].y;
        tmpCnt++;
        
        input.event[tmpCnt].type = EV_ABS;
        input.event[tmpCnt].code = ABS_MT_POSITION_X;
        input.event[tmpCnt].value = Finger[i][j].x;
        tmpCnt++;
        
        input.event[tmpCnt].type = EV_ABS;
        input.event[tmpCnt].code = ABS_MT_POSITION_Y;
        input.event[tmpCnt].value = Finger[i][j].y;
        tmpCnt++;
        
        input.event[tmpCnt].type = EV_ABS;
        input.event[tmpCnt].code = ABS_MT_TRACKING_ID;
        input.event[tmpCnt].value = Finger[i][j].id;
        tmpCnt++;
        
        if (Finger[i][j].size1)
        {
          input.event[tmpCnt].type = EV_ABS;
          input.event[tmpCnt].code = ABS_MT_TOUCH_MAJOR;
          input.event[tmpCnt].value = Finger[i][j].size1;
          tmpCnt++;
        }
        if (Finger[i][j].size2)
        {
          input.event[tmpCnt].type = EV_ABS;
          input.event[tmpCnt].code = ABS_MT_WIDTH_MAJOR;
          input.event[tmpCnt].value = Finger[i][j].size2;
          tmpCnt++;
        }
        if (Finger[i][j].size3)
        {
          input.event[tmpCnt].type = EV_ABS;
          input.event[tmpCnt].code = ABS_MT_TOUCH_MINOR;
          input.event[tmpCnt].value = Finger[i][j].size3;
          tmpCnt++;
        }
        
        input.event[tmpCnt].type = EV_SYN;
        input.event[tmpCnt].code = SYN_MT_REPORT;
        input.event[tmpCnt].value = 0;
        tmpCnt++;
      }
    }
  }
  finish:
  bool is = false;
  if (tmpCnt == 0) {
    input.event[tmpCnt].type = EV_SYN;
    input.event[tmpCnt].code = SYN_MT_REPORT;
    input.event[tmpCnt].value = 0;
    tmpCnt++;
    if (!isFirstDown) {
      isFirstDown = true;
      input.event[tmpCnt].type = EV_KEY;
      input.event[tmpCnt].code = BTN_TOUCH;
      input.event[tmpCnt].value = 0;
      tmpCnt++;
      input.event[tmpCnt].type = EV_KEY;
      input.event[tmpCnt].code = BTN_TOOL_FINGER;
      input.event[tmpCnt].value = 0;
      tmpCnt++;
    }
  } else {
    is = true;
  }
  input.event[tmpCnt].type = EV_SYN;
  input.event[tmpCnt].code = SYN_REPORT;
  input.event[tmpCnt].value = 0;
  tmpCnt++;
  
  if (is && isFirstDown) {
    isFirstDown = false;
    write(nowfd, &input, sizeof(struct input_event) * (tmpCnt + 2));
  } else {
    write(nowfd, input.event, sizeof(struct input_event) * tmpCnt);
  }
  
  bTouch = false;
}

static void *TypeA(void *arg) {
  targ tmp = *(targ *) arg;
  int i = tmp.fdNum;
  float S2TX = tmp.S2TX;
  float S2TY = tmp.S2TY;
  int latest = 0;
  input_event inputEvent[64]{0};
  
  timer touchFPS;
  touchFPS.SetFps(800);
  touchFPS.AotuFPS_init();
  touchFPS.setAffinity();
  
  while (Touch_initialized) {
    ImGuiIO &io = ImGui::GetIO();
    auto readSize = (int32_t) read(origfd[i], inputEvent, sizeof(inputEvent));
    if (readSize <= 0 || (readSize % sizeof(input_event)) != 0) {
      continue;
    }
    size_t count = size_t(readSize) / sizeof(input_event);
    for (size_t j = 0; j < count; j++) {
      input_event &ie = inputEvent[j];
      if (ie.type == EV_ABS) {
        if (ie.code == ABS_MT_SLOT) {
          latest = ie.value;
          Touch_Temporary_SLOT = ie.value;
          Touch_I++;
          continue;
        }
        if (ie.code == ABS_MT_TRACKING_ID) {
          if (ie.value == -1) {
            Finger[i][latest].isDown = false;
            io.MouseDown[0] = false;
            Touch_Temporary_SLOT_Bak = Touch_Temporary_SLOT;
            Touch_ID = -1;
          } else {
            Finger[i][latest].id = (i * 2 + 1) * maxF + latest;
            Touch_ID = ie.value;
            Touch_I_bak = Touch_I;
            Touch_Temporary_SLOT_Bak = Touch_Temporary_SLOT;
            Finger[i][latest].isDown = true;
            io.MouseDown[0] = true;
          }
          Touch_I++;
          continue;
        }
        if (ie.code == ABS_MT_POSITION_X) {
          Finger[i][latest].id = (i * 2 + 1) * maxF + latest;
          Finger[i][latest].x = (int) (ie.value * S2TX);
          if(Touch_I == Touch_I_bak+1) {
            Touch_Clicks.x = (float) ie.value * S2TX;
          }
          Touch_I++;
          Finger[i][latest].isTmpDown = true;
          continue;
        }
        if (ie.code == ABS_MT_POSITION_Y) {
          Finger[i][latest].id = (i * 2 + 1) * maxF + latest;
          Finger[i][latest].y = (int) (ie.value * S2TY);
          
          if(Touch_I == Touch_I_bak+2)
          {
            Touch_Clicks.y = (float) ie.value * S2TY;
            Touch_Clicks = Touch2Screen(Touch_Clicks);
          }
          Touch_I++;
          Finger[i][latest].isTmpDown = true;
          continue;
        }
        if (ie.code == ABS_MT_TOUCH_MAJOR)
        {
          Finger[i][latest].id = (i * 2 + 1) * maxF + latest;
          Finger[i][latest].size1 = ie.value;
          Finger[i][latest].isTmpDown = true;
          continue;
        }
        if (ie.code == ABS_MT_WIDTH_MAJOR)
        {
          Finger[i][latest].id = (i * 2 + 1) * maxF + latest;
          Finger[i][latest].size2 = ie.value;
          Finger[i][latest].isTmpDown = true;
          continue;
        }
        if (ie.code == ABS_MT_TOUCH_MINOR)
        {
          Finger[i][latest].id = (i * 2 + 1) * maxF + latest;
          Finger[i][latest].size3 = ie.value;
          Finger[i][latest].isTmpDown = true;
          continue;
        }
        
      }
      
      
      float x = Finger[i][latest].x, y = Finger[i][latest].y;
      float xt = x / scale_x;
      float yt = y / scale_y;
      if (other_touch) {
        switch (orientation) {
          case 1:
          x = xt;
          y = yt;
          break;
          case 2:
          y = yt;
          x = screenHeight - xt;
          break;
          case 3:
          x = screenHeight - xt;
          y = screenWidth - yt;
          break;
          default:
          y = xt;
          x = screenHeight - yt;
          break;
        }
      } else {
        switch (orientation) {
          case 1:
          x = yt;
          y = screenHeight - xt;
          break;
          case 2:
          x = screenHeight - xt;
          y = screenWidth - yt;
          break;
          case 3:
          y = xt;
          x = screenWidth - yt;
          break;
          default:
          x = xt;
          y = yt;
          break;
        }
      }
      io.MousePos = {x, y};
      
      
      if (io.MousePos.x <= 绘制.Pos.x + 绘制.winWidth && io.MousePos.y <= 绘制.Pos.y + 绘制.winHeith && io.MousePos.x >= 绘制.Pos.x && io.MousePos.y >= 绘制.Pos.y)
      {
        Finger[i][latest].isDown = false;
        Finger[i][latest].isTmpDown = false;
      }
      
      if (ie.type == EV_SYN)
      {
        if (ie.code == SYN_REPORT)
        {
          if (Finger[i][latest].isTmpDown)
          Upload();
          continue;
        }
        continue;
      }
      
      
    }
    touchFPS.SetFps((int)绘制.自瞄.触摸采样率);
    touchFPS.AotuFPS();
  }
  return nullptr;
}


bool Touch_Init(int w, int h, uint32_t orientation_, bool readOnly) {
  char temp[128];
  DIR *dir = opendir("/dev/input/");
  dirent *ptr = NULL;
  int eventCount = 0;
  while ((ptr = readdir(dir)) != NULL) {
    if (strstr(ptr->d_name, "event"))
    eventCount++;
  }
  struct input_absinfo abs, absX[maxE], absY[maxE];
  int fd, i, tmp1, tmp2;
  int screenX, screenY, minCnt = eventCount + 1;
  fdNum = 0;
  for (i = 0; i <= eventCount; i++) {
    sprintf(temp, "/dev/input/event%d", i);
    fd = open(temp, O_RDWR);
    if (fd < 0) {
      continue;
    }
    if (checkDeviceIsTouch(fd)) {
      tmp1 = ioctl(fd, EVIOCGABS(ABS_MT_POSITION_X), &absX[fdNum]);
      tmp2 = ioctl(fd, EVIOCGABS(ABS_MT_POSITION_Y), &absY[fdNum]);
      if (tmp1 == 0 && tmp2 == 0) {
        origfd[fdNum] = fd;
        if (!readOnly) {
          ioctl(fd, EVIOCGRAB, GRAB);
        }
        if (i < minCnt) {
          screenX = absX[fdNum].maximum;
          screenY = absY[fdNum].maximum;
          minCnt = i;
        }
        fdNum++;
        if (fdNum >= maxE)
        break;
      }
    } else {
      close(fd);
    }
  }
  
  if (minCnt > eventCount) {
    puts("获取屏幕驱动失败");
    return false;
  }
  
  if (!readOnly) {
    struct uinput_user_dev ui_dev;
    nowfd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (nowfd <= 0) {
      return false;
    }
    
    int string_len = rand() % 10 + 5;
    char string[string_len];
    memset(&ui_dev, 0, sizeof(ui_dev));
    
    genRandomString(string, string_len);
    strncpy(ui_dev.name, string, UINPUT_MAX_NAME_SIZE);
    
    ui_dev.id.bustype = 0;
    ui_dev.id.vendor = rand() % 10 + 5;
    ui_dev.id.product = rand() % 10 + 5;
    ui_dev.id.version = rand() % 10 + 5;
    
    ioctl(nowfd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);
    
    ioctl(nowfd, UI_SET_EVBIT, EV_ABS);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_X);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_Y);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_MT_POSITION_X);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_MT_POSITION_Y);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_MT_TRACKING_ID);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_MT_TOUCH_MAJOR);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_MT_WIDTH_MAJOR);
    ioctl(nowfd, UI_SET_ABSBIT, ABS_MT_TOUCH_MINOR);
    ioctl(nowfd, UI_SET_EVBIT, EV_SYN);
    ioctl(nowfd, UI_SET_EVBIT, EV_KEY);
    ioctl(nowfd, UI_SET_KEYBIT, BTN_TOOL_FINGER);
    ioctl(nowfd, UI_SET_KEYBIT, BTN_TOUCH);
    
    // 设置设备属性，禁止其他程序访问
    ioctl(nowfd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);
    
    
    
    genRandomString(string, string_len);
    ioctl(nowfd, UI_SET_PHYS, string);
    
    sprintf(temp, "/dev/input/event%d", minCnt);
    fd = open(temp, O_RDWR);
    if (fd) {
      struct input_id id;
      if (!ioctl(fd, EVIOCGID, &id)) {
        ui_dev.id.bustype = id.bustype;
        ui_dev.id.vendor = id.vendor;
        ui_dev.id.product = id.product;
        ui_dev.id.version = id.version;
      }
      uint8_t *bits = NULL;
      ssize_t bits_size = 0;
      int res, j, k;
      while (1) {
        res = ioctl(fd, EVIOCGBIT(EV_KEY, bits_size), bits);
        if (res < bits_size)
        break;
        bits_size = res + 16;
        bits = (uint8_t *) realloc(bits, bits_size * 2);
      }
      for (j = 0; j < res; j++) {
        for (k = 0; k < 8; k++)
        if (bits[j] & 1 << k) {
          if (j * 8 + k == BTN_TOUCH || j * 8 + k == BTN_TOOL_FINGER)
          continue;
          ioctl(nowfd, UI_SET_KEYBIT, j * 8 + k);
        }
      }
      free(bits);
    }
    ui_dev.absmin[ABS_MT_POSITION_X] = 0;
    ui_dev.absmax[ABS_MT_POSITION_X] = screenX;
    ui_dev.absmin[ABS_MT_POSITION_Y] = 0;
    ui_dev.absmax[ABS_MT_POSITION_Y] = screenY;
    ui_dev.absmin[ABS_X] = 0;
    ui_dev.absmax[ABS_X] = screenX;
    ui_dev.absmin[ABS_Y] = 0;
    ui_dev.absmax[ABS_Y] = screenY;
    ui_dev.absmin[ABS_MT_TOUCH_MAJOR] = 0;
    ui_dev.absmax[ABS_MT_TOUCH_MAJOR] = 255;
    ui_dev.absmin[ABS_MT_WIDTH_MAJOR] = 0;
    ui_dev.absmax[ABS_MT_WIDTH_MAJOR] = 255;
    ui_dev.absmin[ABS_MT_TOUCH_MINOR] = 0;
    ui_dev.absmax[ABS_MT_TOUCH_MINOR] = 255;
    ui_dev.absmin[ABS_MT_TRACKING_ID] = 0;
    ui_dev.absmax[ABS_MT_TRACKING_ID] = 65535;
    write(nowfd, &ui_dev, sizeof(ui_dev));
    
    if (ioctl(nowfd, UI_DEV_CREATE)) {
      return false;
    }
    //ioctl(nowfd, UI_DEV_DESTROY);//创建成功后尝试注销
    
  }
  Touch_initialized = true;
  Touch_readOnly = readOnly;
  
  pthread_t t;
  for (i = 0; i < fdNum; i++) {
    targF[i].fdNum = i;
    targF[i].S2TX = (float) screenX / (float) absX[i].maximum;
    targF[i].S2TY = (float) screenY / (float) absY[i].maximum;
    pthread_create(&t, NULL, TypeA, &targF[i]);
  }
  fdNum++;
  ::screenWidth = w;
  ::screenHeight = h,
  ::orientation = orientation_;
  if (::orientation == 1 || ::orientation == 3) {
    ::scale_x = (float) screenX / h;
    ::scale_y = (float) screenY / w;
  } else {
    ::scale_x = (float) screenX / w;
    ::scale_y = (float) screenY / h;
  }
  system("chmod 000 -R /proc/bus/input/*");
  return true;
}
void UpdateScreenData(int w, int h, uint32_t orientation_) {
  ::screenWidth = w;
  ::screenHeight = h,
  ::orientation = orientation_;
}
static bool checkDeviceIsTouch(int fd) {
  uint8_t *bits = NULL;
  ssize_t bits_size = 0;
  int res, j, k;
  bool itmp = false, itmp2 = false, itmp3 = false;
  struct input_absinfo abs{};
  while (true) {
    res = ioctl(fd, EVIOCGBIT(EV_ABS, bits_size), bits);
    if (res < bits_size)
    break;
    bits_size = res + 16;
    bits = (uint8_t *) realloc(bits, bits_size * 2);
  }
  for (j = 0; j < res; j++) {
    for (k = 0; k < 8; k++)
    if (bits[j] & 1 << k && ioctl(fd, EVIOCGABS(j * 8 + k), &abs) == 0) {
      if (j * 8 + k == ABS_MT_SLOT) {
        itmp = true;
        continue;
      }
      if (j * 8 + k == ABS_MT_POSITION_X) {
        itmp2 = true;
        continue;
      }
      if (j * 8 + k == ABS_MT_POSITION_Y) {
        itmp3 = true;
        continue;
      }
    }
  }
  free(bits);
  return itmp && itmp2 && itmp3;
}

void Touch_Down(float x, float y) {
  touchObj &touch = Finger[0][9];
  touch.id = 8;
  touch.x = (int) (x * scale_x);
  touch.y = (int) (y * scale_y);
  touch.isDown = true;
  touch.size1 = 8;
  touch.size2 = 8;
  touch.size3 = 8;
  Upload();
}
void Touch_Move(float x, float y) {
  Touch_Down(x, y);
}
void Touch_Up() {
  //printf("测试 %d / %d\n",maxE,maxF);
  touchObj &touch = Finger[0][9];
  touch.isDown = false;
  Upload();
  Upload();
}


// 判断点是否在矩形内（包含边界）
static bool contains(float x, float y, float orignalx, float orignaly,float width)
{
    float rectX = orignalx;
    float rectY = orignaly;
    float rectWidth = width;
    float rectHeight = width;
    return x >= rectX && x < rectX + rectWidth &&
        y >= rectY && y < rectY + rectHeight;
}

static spinlock spin_lock;

bool clickRegion(bool isClick, float x, float y, float 范围)
{
    static std::chrono::steady_clock::time_point lastClickTime;
    // 连点频率， 每秒4-5次点击速度
    setClickFrequencyRange(4.0f * 100.0f, 5.0f * 100.0f);

    int i, j = 0;

    for (i = 0; i < fdNum; i++)
    {
        for (j = 0; j < maxF; j++)
        {
            if (Finger[i][j].isDown)
            {
                Vector2 vec = { Finger[i][j].x, Finger[i][j].y };
                auto pos = Touch2Screen(vec);
                float mouseX = pos.x;
                float mouseY = pos.y;

                if (mouseX < 2500.0 || mouseY < 1700.0)
                    continue;

               
                if (contains(mouseX, mouseY, x,y, 范围))
                {
                  //  printf("点击位置在开火范围内！\n");
                    
                    spin_lock.lock();
                    auto now = std::chrono::steady_clock::now();
                    // 动态生成随机间隔
                    auto requiredInterval = getRandomInterval();
                    if (now - lastClickTime >= requiredInterval && isClick)
                    {
                        
                        // 抬起用户连点位置手指
                        Finger[i][j].isDown = false;
                        Upload();
                        Finger[i][j].isDown = true;
                        lastClickTime = now;
                      //  printf("触发连点\n");
                    }
                    spin_lock.unlock();
                    return true;
                }
                else
                {

                }
            }
        }
    }
    return false;
}

static int cnt = 0;
static int TMPcnt = 0;
// 检测右边开火键是否被按下
bool 检测开火键长按(float 开火范围x, float 开火范围y, float 开火范围)
{
    // 计算区域中心（注意：开火范围y已经是顶部向下的坐标）
    float 中心X = 开火范围x + 开火范围 / 2;
    float 中心Y = 开火范围y + 开火范围 / 2;
    float 半径 = 开火范围 * 0.75; // 或者根据需要调整，例如开火范围 * 0.6

    for (int i = 0; i < fdNum; i++)
    {
        for (int j = 0; j < maxF; j++)
        {
            //模拟触摸使用的是固定的手指槽（Finger[0][9]），其ID固定为8
            if (Finger[i][j].isDown && Finger[i][j].id != 8 && Finger[i][j].id != 1000)
            {
 
                Vector2 vec = { Finger[i][j].x, Finger[i][j].y };
                  // 解锁，因为Touch2Screen可能耗时，且不涉及共享数据
                auto pos = Touch2Screen(vec);

                float dx = pos.x - 中心X;
                float dy = pos.y - 中心Y;
                float 距离 = sqrt(dx * dx + dy * dy);

                /*printf("手指(%.0f,%.0f) 中心(%.0f,%.0f) 距离=%.0f 半径=%.0f %s\n",
                    pos.x, pos.y, 中心X, 中心Y, 距离, 半径,
                    距离 <= 半径 ? "在区域内" : "不在");*/

                if (距离 <= 半径)
                {
                    return true;
                }
                //float mouseX = pos.x;
                //float mouseY = pos.y;

                //// 检查是否在右边区域内
                //if (mouseX >= 开火范围x && mouseX <= 开火范围x + 开火范围 &&
                //    mouseY >= 开火范围y && mouseY <= 开火范围y + 开火范围)
                //{
                //   /* printf("右手在区域内: (%.0f,%.0f) 区域: (%.0f-%.0f, %.0f-%.0f)\n",
                //        pos.x, pos.y, 开火范围x, 开火范围x + 开火范围, 开火范围y, 开火范围y + 开火范围);*/
                //    printf("右手开火键被按下,cnt=%d\n",++cnt);
                //    return true;
                //}
                //else
                //{
                //    printf("右手开火键未被按下,手指(%.0f,%.0f) 区域: (%.0f-%.0f, %.0f-%.0f) , tmp=%d\n", pos.x, pos.y, 开火范围x, 开火范围x + 开火范围, 开火范围y, 开火范围y + 开火范围, ++TMPcnt);
                //}

                 
            }
        }
    }
    return false;
}


// 模拟点击左边开火键
void 模拟左边开火键点击(float 连点范围x, float 连点范围y, float 连点范围)
{
    // 计算左边区域的中心点（屏幕坐标）
    float 左边中心X = 连点范围x + 连点范围 / 2;
    float 左边中心Y = 连点范围y + 连点范围 / 2;

    // 将屏幕坐标转换为触摸坐标
    Vector2 屏幕坐标 = { 连点范围x, 连点范围y };
    Vector2 触摸坐标 = Screen2Touch(屏幕坐标);

    /*printf("模拟点击: 屏幕坐标(%.0f, %.0f) -> 触摸坐标(%.0f, %.0f)\n",
        连点范围x, 连点范围y, 触摸坐标.x, 触摸坐标.y);*/

    

    // 找到空闲的手指槽
    bool 找到空闲位置 = false;

    
    for (int i = 0; i < fdNum && !找到空闲位置; i++)
    {
        for (int j = 0; j < maxF; j++)
        {
            
            if (!Finger[i][j].isDown&&!Finger[i][j].isTmpDown)
            {
                spin_lock.lock();
                // 设置虚拟手指
                Finger[i][j].x = 触摸坐标.x;
                Finger[i][j].y = 触摸坐标.y;
                Finger[i][j].id = 1000; // 设置唯一ID
                Finger[i][j].isDown = true;
                Finger[i][j].isTmpDown = true;

                // 触发按下事件
                Upload();

                // 立即释放虚拟手指
                Finger[i][j].isDown = false;
                Finger[i][j].isTmpDown = false;

                // 触发抬起事件
                Upload();

                spin_lock.unlock();

                找到空闲位置 = true;
                
                break;
            }
            
        }
    }
    
    

    if (!找到空闲位置)
    {
        //printf("警告: 没有空闲的手指槽位\n");
    }
}



void 双开火连点控制(float 开火范围x, float 开火范围y, float 开火范围, float 连点范围x, float 连点范围y, float 连点范围)
{
    static bool 右边长按中 = false;
    static std::chrono::steady_clock::time_point 长按开始时间;
    static std::chrono::steady_clock::time_point 上次连点时间;

    bool 当前开火键按下 = 检测开火键长按(开火范围x, 开火范围y, 开火范围);
    auto 当前时间 = std::chrono::steady_clock::now();

    if (当前开火键按下)
    {
        if (!右边长按中)
        {
            // 开始长按
            右边长按中 = true;
            长按开始时间 = 当前时间;
            //printf("右边长按开始 at %lld ms\n", std::chrono::duration_cast<std::chrono::milliseconds>(当前时间.time_since_epoch()).count());
        }
        else
        {
            // 检查长按时间是否超过阈值（例如200ms）
            auto 长按持续时间 = std::chrono::duration_cast<std::chrono::milliseconds>(
                当前时间 - 长按开始时间);

            if (长按持续时间.count() > 100)
            {
                // 触发连点（例如每秒10次）
                auto 连点间隔 = std::chrono::milliseconds(40); // 100ms = 10次/秒
                auto 时间间隔 = std::chrono::duration_cast<std::chrono::milliseconds>(
                    当前时间 - 上次连点时间);


                 
                if (时间间隔 >= 连点间隔)
                {
                    // 模拟点击左边开火键
                    模拟左边开火键点击(连点范围x, 连点范围y, 连点范围);
                    上次连点时间 = 当前时间;

                    static int 连点计数 = 0;
                    // printf("连点触发 #%lld at %lld ms\n", ++连点计数, std::chrono::duration_cast<std::chrono::milliseconds>(当前时间.time_since_epoch()).count());
                }
            }
        }
    }
    else
    {
        if (右边长按中)
        {
            // 停止长按
            右边长按中 = false;
            // printf("右边长按结束 at %lld ms\n", std::chrono::duration_cast<std::chrono::milliseconds>(当前时间.time_since_epoch()).count());
        }


    }
}
