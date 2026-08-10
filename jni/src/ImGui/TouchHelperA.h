#pragma once

#include "VectorStruct.h"

extern int Touch_ID;
extern int Touch_I;
extern int Touch_I_bak;
extern Vector2 Touch_Clicks;    
extern int Touch_Global_SLOT;
extern int Touch_Temporary_SLOT;
extern int Touch_Temporary_SLOT_Bak;

bool Touch_Init(int w, int h, uint32_t orientation_, bool readOnly);
void UpdateScreenData(int w, int h, uint32_t orientation_);

void Touch_Close();
void Touch_Down(float x, float y);
void Touch_Move(float x, float y);
void Touch_Up();

bool clickRegion(bool isClick, float x, float y, float 范围);

bool 检测开火键长按(float 开火范围x, float 开火范围y, float 开火范围);

void 双开火连点控制(float 开火范围x, float 开火范围y, float 开火范围, float 连点范围x, float 连点范围y, float 连点范围);
