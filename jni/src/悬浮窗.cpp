#include "imgui.h"
#include "图片调用.h"
#include "辅助类.h"

extern 绘制 绘制;
static bool 悬浮窗 = false;
static bool 悬浮球 = true;
static bool 自瞄控件 = false;
static int style_idx = 3;
// ImVec2 Pos; //窗口位置
ImVec2 Pos2;
ImVec2 windowSize, windowSize_max;
static bool IsBall = false;
static bool 窗口状态 = false;
static bool show_ChildMenu1 = true;
static bool show_ChildMenu2 = false;
static bool show_ChildMenu3 = false;
static bool show_ChildMenu4 = false;
static bool show_ChildMenu5 = false;
// 在全局变量部分添加以下变量
//static bool 巡查员提示窗口 = true;
static ImVec2 巡查员窗口位置 = ImVec2(0, 0);
static bool 正在拖动巡查员窗口 = false;
static int 自瞄窗口 = 0;
static int 配置窗口 = 0;
static int 物资窗口 = 0;
//static bool 广角设置 = false;

ImU32 c_透明 = IM_COL32(0, 0, 0, 0);
ImU32 c_fafafa = IM_COL32(250, 250, 250, 255);
ImU32 c_cccccc = IM_COL32(204, 204, 204, 255);
ImU32 c_c2c2c2 = IM_COL32(194, 194, 194, 255);
ImU32 c_23292e = IM_COL32(35, 41, 46, 255);
ImU32 c_4023292e = IM_COL32(35, 41, 46, 125);
ImU32 c_eeeeee = IM_COL32(0xee, 0xee, 0xee, 255);
ImU32 c_2f363c = IM_COL32(47, 54, 60, 255);
ImU32 c_402f363c = IM_COL32(47, 54, 60, 125);
ImU32 c_DAB123 = IM_COL32(218, 177, 35, 255);
ImU32 c_DCD4CA = IM_COL32(220, 212, 202, 255);
ImU32 c_2C88E2 = IM_COL32(44, 136, 226, 255);
ImU32 c_2C88E240 = IM_COL32(44, 136, 226, 128);
ImU32 c_545D6D = IM_COL32(84, 93, 109, 255);
ImU32 c_545D6D40 = IM_COL32(84, 93, 109, 128);
ImU32 c_FFD49F = IM_COL32(255, 212, 159, 255);
ImU32 c_FFD49F80 = IM_COL32(255, 212, 159, 128);
ImU32 c_16b777 = IM_COL32(0x16, 0xB7, 0x77, 255);
ImU32 c_16b77780 = IM_COL32(0x16, 0xB7, 0x77, 128);
ImU32 c_11243B = IM_COL32(0x11, 0x24, 0x3B, 255);
ImU32 c_11243B80 = IM_COL32(0x11, 0x24, 0x3B, 128);
ImU32 c_31bdec = IM_COL32(0x31, 0xbd, 0xec, 255);
ImU32 c_558FFD = IM_COL32(0x55, 0x8F, 0xFD, 255);
ImU32 c_81A4DC = IM_COL32(0x81, 0xa4, 0xdc, 255);
ImVec4 透明ImVec4 = ImVec4(0, 0, 0, 0);
ImVec4 黑色ImVec4 = ImVec4(0, 0, 0, 1.0f);
ImVec4 灰色ImVec4 = ImVec4(0.1f, 0.1f, 0.1f, 1.0f);
ImVec4 灰色ImVec440 = ImVec4(0.1f, 0.1f, 0.1f, 0.5f);
ImVec4 灰色ImVec480 = ImVec4(0.1f, 0.1f, 0.1f, 0.75f);
ImVec4 亮灰色ImVec4 = ImVec4(0.5, 0.5, 0.5, 1);
ImVec4 亮黄色ImVec4 = ImVec4(1.0f, 0.831f, 0.623f, 1.0f);
ImVec4 半透明黑色ImVec4 = ImVec4(0.184314f, 0.211765f, 0.235294f, 0.5f);
ImVec4 半透明黑色ImVec480 = ImVec4(0.184314f, 0.211765f, 0.235294f, 0.75f);
ImVec4 ImVec423292e = ImVec4(0.137255f, 0.160784f, 0.180392f, 1.0f);
ImVec4 ImVec80423292e = ImVec4(0.137255f, 0.160784f, 0.180392f, 0.75f);
ImVec4 ImVec40423292e = ImVec4(0.137255f, 0.160784f, 0.180392f, 0.5f);
ImVec4 白色ImVec4 = ImVec4(1, 1, 1, 1);
ImVec4 ImVec4fafafa = ImVec4(0xfa / 255.0f, 0xfa / 255.0f, 0xfa / 255.0f, 1.0f);
ImVec4 ImVec431bdec = ImVec4(0x31 / 255.0f, 0xbd / 255.0f, 0xec / 255.0f, 1.0f);
ImVec4 ImVec416b777 = ImVec4(0x16 / 255.0f, 0xB7 / 255.0f, 0x77 / 255.0f, 1.0f);
ImVec4 ImVec4c2c2c2 = ImVec4(0xC2 / 255.0f, 0xC2 / 255.0f, 0xC2 / 255.0f, 1.0f);
ImVec4 ImVec42C88E2 = ImVec4(0x2C / 255.0f, 0x88 / 255.0f, 0xE2 / 255.0f, 1.0f);
ImVec4 ImVec411243B = ImVec4(0x11 / 255.0f, 0x24 / 255.0f, 0x3B / 255.0f, 1.0f);
ImVec4 ImVec411243B80 = ImVec4(0x11 / 255.0f, 0x24 / 255.0f, 0x3B / 255.0f, 0.75f);
ImVec4 ImVec411243B40 = ImVec4(0x11 / 255.0f, 0x24 / 255.0f, 0x3B / 255.0f, 0.5f);
ImVec4 ImVec4f3c258 = ImVec4(0xf3 / 255.0f, 0xc2 / 255.0f, 0x58 / 255.0f, 1.0f);
ImVec4 ImVec4eeeeee = ImVec4(0xee / 255.0f, 0xee / 255.0f, 0xee / 255.0f, 1.0f);
ImVec4 ImVec4558FFD = ImVec4(0x55 / 255.0f, 0x8f / 255.0f, 0xff / 255.0f, 1.0f);
ImVec4 ImVec481A4DC = ImVec4(0x81 / 255.0f, 0xa4 / 255.0f, 0xdc / 255.0f, 1.0f);

void CustomNewLine(float lineHeight = -1.0f)
{
	if (lineHeight < 0.0f)
	{
		// 使用默认的行高
		ImGui::NewLine();
	}
	else
	{
		// 设置自定义行高
		ImGui::Dummy(ImVec2(0.0f, lineHeight));
	}
}

void RadioGroup(const char* label, int* selectedIndex, const char* options[], int numOptions, float width, float fontSize = 1.0f, float padding = 10.0f)
{
	ImGui::SetWindowFontScale(fontSize);
	ImGui::PushID(label);
	const ImGuiStyle& style = ImGui::GetStyle();
	const ImVec2 startPos = ImGui::GetCursorScreenPos();
	const float buttonSpacing = style.ItemSpacing.x;
	const float borderRadius = 10.0f;
	const ImVec2 buttonStartPos = ImVec2(startPos.x + padding, startPos.y + padding + style.FramePadding.y);
	const ImU32 selectedColor = c_558FFD;
	const ImU32 unselectedTextColor = c_4023292e;
	const ImU32 selectedTextColor = c_fafafa;
	const float buttonWidth = (width - buttonSpacing * (numOptions - 1)) / numOptions;
	ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 0.0f);
	ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, borderRadius);
	ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0.0f, style.ItemSpacing.y));
	float totalWidth = 0.0f;
	for (int i = 0; i < numOptions; ++i)
	{
		totalWidth += buttonWidth;
	}
	const ImVec2 bgSize(totalWidth + padding * 2.0f, style.FramePadding.y * 2.0f + ImGui::GetTextLineHeight() + padding * 2.0f);
	const ImVec2 bgStartPos = ImVec2(startPos.x - padding, startPos.y - padding);
	const ImU32 shadowColor = IM_COL32(0, 0, 0, 64);
	const ImVec2 shadowOffset(3.0f, 3.0f);
	const ImVec2 shadowStartPos = ImVec2(bgStartPos.x + shadowOffset.x, bgStartPos.y + shadowOffset.y);
	const ImVec2 shadowEndPos = ImVec2(shadowStartPos.x + bgSize.x, shadowStartPos.y + bgSize.y);
	// ImGui::GetWindowDrawList()->AddRectFilled(shadowStartPos, shadowEndPos, shadowColor, style.FrameRounding);
	ImGui::GetWindowDrawList()->AddRectFilled(bgStartPos, ImVec2(bgStartPos.x + bgSize.x, bgStartPos.y + bgSize.y), c_eeeeee, style.FrameRounding);
	for (int i = 0; i < numOptions; ++i)
	{
		if (i > 0)
		{
			ImGui::SameLine();
		}
		if (*selectedIndex == i)
		{
			ImGui::PushStyleColor(ImGuiCol_Button, selectedColor);
			ImGui::PushStyleColor(ImGuiCol_ButtonHovered, selectedColor);
			ImGui::PushStyleColor(ImGuiCol_ButtonActive, selectedColor);
			ImGui::PushStyleColor(ImGuiCol_Text, selectedTextColor);
		}
		else
		{
			ImGui::PushStyleColor(ImGuiCol_Button, IM_COL32(0, 0, 0, 0));         // 透明
			ImGui::PushStyleColor(ImGuiCol_ButtonHovered, IM_COL32(0, 0, 0, 0));  // 透明
			ImGui::PushStyleColor(ImGuiCol_ButtonActive, IM_COL32(0, 0, 0, 0));   // 透明
			ImGui::PushStyleColor(ImGuiCol_Text, unselectedTextColor);
		}
		if (ImGui::Button(options[i], ImVec2(buttonWidth, 0.0f)))
		{
			*selectedIndex = i;
		}
		ImGui::PopStyleColor(4);
	}
	ImGui::PopStyleVar(2);
	ImGui::PopStyleColor();
	ImGui::PopID();
	ImGui::SetWindowFontScale(1.0f);
}

void CustomText(const char* text, ImVec2 size, int padding = 5, int alignment = 0, float fontSize = 1.5f, bool showShadow = false, ImVec4 bgColor = ImVec4(0.0f, 0.0f, 0.0f, 0.5f), ImVec4 textColor = ImVec40423292e, ImVec4 shadowColor = ImVec4(0.0f, 0.0f, 0.0f, 0.5f))
{
	ImGui::SetWindowFontScale(fontSize);
	ImGuiWindow* window = ImGui::GetCurrentWindow();
	if (window->SkipItems)
		return;
	const ImGuiStyle& style = ImGui::GetStyle();
	const ImGuiID id = window->GetID(text);
	const ImVec2 contentMin = window->DC.CursorPos;
	const ImVec2 contentMax = ImVec2(contentMin.x + size.x, contentMin.y + size.y);
	if (showShadow)
	{
		ImDrawList* drawList = ImGui::GetWindowDrawList();
		ImU32 shadowColor2 = IM_COL32(0, 0, 0, 128);
		float shadowStartY = contentMin.y + size.y - 3;
		float shadowEndY = shadowStartY + 5;
		drawList->AddRectFilledMultiColor(
			ImVec2(contentMin.x, shadowStartY),
			ImVec2(contentMin.x + size.x, shadowEndY),
			shadowColor2, shadowColor2, shadowColor2, shadowColor2);
	}
	if (bgColor.w > 0.0f)
	{
		window->DrawList->AddRectFilled(contentMin, contentMax, ImGui::GetColorU32(bgColor));
	}
	ImVec2 textSize = ImGui::CalcTextSize(text);
	ImVec2 textPos;
	switch (alignment)
	{
	case 1:
		textPos = ImVec2(contentMax.x - textSize.x - padding, contentMin.y + (size.y - textSize.y) * 0.5f);
		break;
	case 2:
		textPos = ImVec2(contentMin.x + (size.x - textSize.x) * 0.5f, contentMin.y + (size.y - textSize.y) * 0.5f);
		break;
	default:
		textPos = ImVec2(contentMin.x + padding, contentMin.y + (size.y - textSize.y) * 0.5f);
		break;
	}
	window->DrawList->AddText(textPos, ImGui::GetColorU32(textColor), text);
	float textHeightWithPadding = textSize.y + padding * 2;
	ImGui::ItemSize(ImVec2(size.x, textHeightWithPadding), style.FramePadding.y);
	if (!ImGui::ItemAdd(ImRect(contentMin, ImVec2(contentMax.x, contentMin.y + textHeightWithPadding)), id))
		return;
	ImGui::SetWindowFontScale(1.0f);
}

void 绘制背景图(TextureInfo textureInfo, ImVec2 center, float size)
{
	ImGui::SetCursorPos({ 0, 180 });
	ImDrawList* draw_list = ImGui::GetWindowDrawList();
	draw_list->AddImage(textureInfo.textureId, { center.x - size / 2, center.y - size / 2 }, { center.x + size / 2, center.y + size / 2 });
}

void 布局::绘制悬浮窗()
{
	drawBegin();


	if (绘制.按钮.绘制)
	{
		//printf("运行绘制\n");
		绘制.运行绘制();
	}


	//绘制自瞄开关按键
	if (绘制.自瞄.自瞄控件)
	{
		ImGui::SetNextWindowSize({ 200, 200 });
		if (ImGui::Begin("自瞄控件", &自瞄控件, ImGuiWindowFlags_NoBackground | ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoScrollbar))
		{
			auto Pos = ImGui::GetWindowPos();

			static bool isMouseDown = false;
			static bool wasMousePressed = false;
			static bool isTe = false;
			static ImVec2 mousePressPos;
			bool mouseDown = ImGui::IsMouseDown(ImGuiMouseButton_Left);
			bool windowHovered = ImGui::IsWindowHovered();

			if (mouseDown && !isMouseDown && windowHovered && ImGui::IsMouseHoveringRect(Pos, { Pos.x + 100, Pos.y + 100 }))
			{
				isMouseDown = true;
				wasMousePressed = true;
				mousePressPos = ImGui::GetMousePos();
			}
			else if (!mouseDown && isMouseDown && wasMousePressed)
			{
				ImVec2 mouseReleasePos = ImGui::GetMousePos();
				if (mousePressPos.x == mouseReleasePos.x && mousePressPos.y == mouseReleasePos.y)
				{
					isTe = !isTe;
				}

				isMouseDown = false;
				wasMousePressed = false;
			}

			if (isTe)
			{
				绘制.自瞄.初始化 = true;
				ImGui::GetWindowDrawList()->AddImage(手持图片[4].textureId, { Pos.x + 20, Pos.y + 20 }, { Pos.x + 120, Pos.y + 120 });
			}
			else
			{
				绘制.自瞄.初始化 = false;
				ImGui::GetWindowDrawList()->AddImage(手持图片[3].textureId, { Pos.x + 20, Pos.y + 20 }, { Pos.x + 120, Pos.y + 120 });
			}
		}
		ImGui::End();
	}

	if (悬浮球)
	{
		ImGui::SetNextWindowSize({ 200, 200 });
		if (ImGui::Begin("悬浮图片", &悬浮球, ImGuiWindowFlags_NoBackground | ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoScrollbar))
		{
			绘制.winWidth = ImGui::GetWindowWidth();
			绘制.winHeith = ImGui::GetWindowHeight();
			if (窗口状态)
			{
				ImGui::SetWindowPos("悬浮图片", 绘制.Pos, ImGuiCond_Always);
				窗口状态 = false;
			}
			绘制.Pos = ImGui::GetWindowPos();
			ImGui::GetWindowDrawList()->AddImage(手持图片[60].textureId, { 绘制.Pos.x + 20, 绘制.Pos.y + 20 }, { 绘制.Pos.x + 120, 绘制.Pos.y + 120 });

			static bool isDragging = false;
			if (ImGui::IsItemActive())
			{
				if (!isDragging)
				{
					isDragging = true;
					Pos2 = ImGui::GetWindowPos();
				}
			}
			else if (isDragging)
			{
				isDragging = false;
				if (Pos2.x == ImGui::GetWindowPos().x && Pos2.y == ImGui::GetWindowPos().y)
				{
					悬浮球 = false;
					悬浮窗 = true;
					窗口状态 = true;
					IsBall = true;
					ImGui::SetWindowPos(绘制.悬浮窗标题, 绘制.Pos, ImGuiCond_Always);
				}
			}
		}
		ImGui::End();
	}

	if (悬浮窗)
	{
		ImGuiStyle& style = ImGui::GetStyle();
		style.WindowRounding = 20.0f;  // 窗口圆角半径
		ImVec2 minSize = ImVec2(1000, 800);  // 悬浮窗初始大小
		ImVec2 maxSize = ImVec2(FLT_MAX, FLT_MAX);
		ImGui::SetNextWindowSizeConstraints(minSize, maxSize);
		if (窗口状态)
		{
			ImGui::SetWindowPos(绘制.悬浮窗标题, 绘制.Pos, ImGuiCond_Always);
			窗口状态 = false;
		}
		绘制.Pos = ImGui::GetWindowPos();

		ImGui::Begin("##test", &悬浮窗, ImGuiWindowFlags_NoCollapse);

		static int selectedMenu = 3;

		ImGui::BeginChild("##菜单", ImVec2(200, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoScrollbar);

		if (ImGui::Button("主页", ImVec2(200, 100)))
		{
			selectedMenu = 3;
		}

		if (ImGui::Button("绘制", ImVec2(200, 100)))
		{
			selectedMenu = 0;
		}

		if (ImGui::Button("自瞄", ImVec2(200, 100)))
		{
			selectedMenu = 1;
		}

		if (ImGui::Button("压枪", ImVec2(200, 100)))
		{
			selectedMenu = 4;
		}

		if (ImGui::Button("预判", ImVec2(200, 100)))
		{
			selectedMenu = 5;
		}

		if (ImGui::Button("物资", ImVec2(200, 100)))
		{
			selectedMenu = 2;
		}

		ImGui::EndChild();
		ImGui::SameLine();


		switch (selectedMenu)
		{
		case 3: {
			ImGui::BeginChild("##系统", ImVec2(-1, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);
			ImGui::Text("欢迎使用小草莓内核");
			ImGui::SliderInt("帧数调节", &绘制.按钮.帧率选项, 60, 144);
			ImGui::SliderFloat("触摸跟手率", &绘制.自瞄.触摸采样率, 200, 1200, "%.0f");

			if (ImGui::Button("初始化", ImVec2(-1, 60)))
			{
				绘制.初始化绘制("com.tencent.tmgp.pubgmhd", abs_ScreenX, abs_ScreenY);
				绘制.按钮.绘制 = true;
			}

			if (ImGui::Button("退出程序", ImVec2(-1, 60)))
				exit(1);
			if (ImGui::Button("删除配置", { -1,70 }))
			{

				system("rm -rf /data/配置文件");
				system("rm -rf /data/颜色配置文件");
			}
			if (ImGui::Button("漏手模式", ImVec2(-1, 60)))
			{
				// 关闭所有绘制功能
				绘制.按钮.人数 = false;
				绘制.按钮.全图人数 = false;
				绘制.按钮.方框 = false;
				绘制.按钮.血量 = false;
				绘制.按钮.手持 = false;
				绘制.按钮.盒子 = false;
				绘制.按钮.背敌预警 = false;
				绘制.按钮.距离 = false;
				绘制.按钮.射线 = false;
				绘制.按钮.名字 = false;
				绘制.按钮.骨骼 = false;
				绘制.按钮.车辆 = false;
				绘制.按钮.雷达 = false;
				绘制.按钮.载具血量 = false;
				绘制.按钮.载具油量 = false;
				绘制.按钮.手雷预警 = false;
				绘制.自瞄.自瞄控件 = false;
				绘制.自瞄.准星射线 = false;
				绘制.自瞄.隐藏自瞄圈 = true;
				悬浮球 = false; // 添加隐藏悬浮球    
			}
		
			if (ImGui::Button("一键开启", ImVec2(-1, 60)))
			{
				// 开启所有绘制功能
				绘制.按钮.人数 = true;
				绘制.按钮.全图人数 = true;
				绘制.按钮.方框 = true;
				绘制.按钮.血量 = true;
				绘制.按钮.手持 = true;
				绘制.按钮.盒子 = true;
				绘制.按钮.背敌预警 = true;
				绘制.按钮.距离 = true;
				绘制.按钮.射线 = true;
				绘制.按钮.名字 = true;
				绘制.按钮.骨骼 = true;
				绘制.按钮.车辆 = true;
				绘制.按钮.雷达 = true;
				绘制.按钮.载具血量 = true;
				绘制.按钮.载具油量 = true;
				绘制.按钮.手雷预警 = true;
				绘制.自瞄.自瞄控件 = true;
				绘制.自瞄.准星射线 = true;
				绘制.自瞄.隐藏自瞄圈 = false;
				悬浮球 = false; // 添加隐藏悬浮球    
			}



			ImGui::EndChild();
			break;
		}
		case 0: {
			ImGui::BeginChild("##绘制", ImVec2(-1, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			static int DrawMenu = 0;

			ImGui::BeginChild("##绘制菜单", ImVec2(-1, 75), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			if (ImGui::Button("基础设置"))
			{
				DrawMenu = 0;
			}
			ImGui::SameLine();
			if (ImGui::Button("解密"))
			{
				DrawMenu = 2;
			}
			ImGui::SameLine();
			if (ImGui::Button("颜色调节"))
			{
				DrawMenu = 3;
			}
			ImGui::SameLine();
			if (ImGui::Button("粗细调节"))
			{
				DrawMenu = 4;
			}

			ImGui::SameLine();
			ImGui::EndChild();

			switch (DrawMenu)
			{
			case 3: {
				static int ColorSettings = 1;
				ImGui::Text("颜色配置");
				ImGui::Combo("当前配置", &ColorSettings, "人机\0真人\0");

				if (ColorSettings == 1)
				{
					if (ImGui::ColorEdit4("方框颜色", 绘制.Colorset[0].方框颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("射线颜色", 绘制.Colorset[0].射线颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("骨骼颜色", 绘制.Colorset[0].骨骼颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();

					if (ImGui::ColorEdit4("血量颜色", 绘制.Colorset[0].血量颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("阵营颜色", 绘制.Colorset[0].阵营颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("距离颜色", 绘制.Colorset[0].距离颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("名称颜色", 绘制.Colorset[0].名称颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
				}
				else
				{
					if (ImGui::ColorEdit4("方框颜色", 绘制.Colorset[1].方框颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("射线颜色", 绘制.Colorset[1].射线颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("骨骼颜色", 绘制.Colorset[1].骨骼颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("血量颜色", 绘制.Colorset[1].血量颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("阵营颜色", 绘制.Colorset[1].阵营颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("距离颜色", 绘制.Colorset[1].距离颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
					if (ImGui::ColorEdit4("名称颜色", 绘制.Colorset[1].名称颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
						绘制.保存配置();
				}
				if (ImGui::ColorEdit4("手持颜色", 绘制.手持颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
					绘制.保存配置();
				if (ImGui::ColorEdit4("物资颜色", 绘制.物资颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
					绘制.保存配置();
				if (ImGui::ColorEdit4("车辆颜色", 绘制.车辆颜色, ImGuiColorEditFlags_NoBorder | ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_DisplayHex))
					绘制.保存配置();

				break;
			}
			case 4: {
				ImGui::Text("绘图粗细配置");
				ImGui::SliderFloat("方框粗细", &绘制.按钮.方框粗细, 0.1, 5, "%.1f");
				ImGui::SliderFloat("射线粗细", &绘制.按钮.射线粗细, 0.1, 5, "%.1f");
				ImGui::SliderFloat("骨骼粗细", &绘制.按钮.骨骼粗细, 0.1, 5, "%.1f");
				break;
			}
			case 0: {
				ImGui::SameLine();
				static bool 全部开启 = false;
				if (全部开启)
				{
					绘制.按钮.人数 = true;
					绘制.按钮.全图人数 = true;
					绘制.按钮.方框 = true;
					绘制.按钮.血量 = true;
					绘制.按钮.距离 = true;
					绘制.按钮.射线 = true;
					绘制.按钮.名字 = true;
					绘制.按钮.骨骼 = true;
					绘制.按钮.手持 = true;
					绘制.按钮.车辆 = true;
					绘制.按钮.手雷预警 = true;
					绘制.按钮.盒子 = true;
					绘制.按钮.背敌预警 = true;
				}
				ImGui::Separator();
				ImGui::BulletText("人物绘制");
				if (ImGui::Checkbox("人数", &绘制.按钮.人数))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("方框", &绘制.按钮.方框))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("血量", &绘制.按钮.血量))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("手持", &绘制.按钮.手持))
					绘制.保存配置();

				if (ImGui::Checkbox("盒子", &绘制.按钮.盒子))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("背敌", &绘制.按钮.背敌预警))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("距离", &绘制.按钮.距离))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("射线", &绘制.按钮.射线))
					绘制.保存配置();

				if (ImGui::Checkbox("名字", &绘制.按钮.名字))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("骨骼", &绘制.按钮.骨骼))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("车辆", &绘制.按钮.车辆))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("雷达", &绘制.按钮.雷达))
					绘制.保存配置();

				if (ImGui::Checkbox("载具血量", &绘制.按钮.载具血量))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("载具油量", &绘制.按钮.载具油量))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("手雷预警", &绘制.按钮.手雷预警))
					绘制.保存配置();
				
				if (ImGui::Checkbox("忽略人机", &绘制.按钮.忽略人机))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("全图人数", &绘制.按钮.全图人数))
					绘制.保存配置();

				ImGui::BulletText("绘图调节");
				if (ImGui::Combo("手持绘图样式", &绘制.按钮.手持绘图, "手持武器文字\0手持武器图片\0"))
					绘制.保存配置();
				if (ImGui::Combo("血条绘图样式", &绘制.按钮.血条绘图, "默认样式\0IOS样式\0OE样式\0"))
					绘制.保存配置();
				if (ImGui::SliderFloat("骨骼显示距离", &绘制.骨骼距离限制, 0.0f, 300.0f, "%.0f", 1))
					绘制.保存配置();
				if (ImGui::SliderFloat("雷达左右位置", &绘制.按钮.雷达X, 0.0f, 2400.0f, "%.1f", 1))
					绘制.保存配置();
				if (ImGui::SliderFloat("雷达上下位置", &绘制.按钮.雷达Y, 0.0f, 1080.0f, "%.1f", 2))
					绘制.保存配置();
				break;
			}

			case 2: {
				if (ImGui::Button("解密1"))
				{
					绘制.解密模式 = 0x4000;
					绘制.InitMaps();
				}
				ImGui::SameLine();
				if (ImGui::Button("解密2", ImVec2(-1, 60)))
				{
					绘制.解密模式 = 0x20000;
					绘制.InitMaps();
				}
				break;
			}
			}
			ImGui::EndChild();
			break;
		}

		case 1: {
			ImGui::BeginChild("##自瞄", ImVec2(-1, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			static int AimMenu = 0;

			ImGui::BeginChild("##自瞄菜单", ImVec2(-1, 75), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			if (ImGui::Button("触摸自瞄"))
			{
				AimMenu = 0;
			}
			ImGui::SameLine();
			if (ImGui::Button("调节"))
			{
				AimMenu = 2;
			}
			ImGui::SameLine();
			if (ImGui::Button("打击部位"))
			{
				AimMenu = 1;
			}

			ImGui::EndChild();
			switch (AimMenu)
			{
			case 0: {
				if (ImGui::Checkbox("打开自瞄", &绘制.自瞄.初始化))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("自瞄快捷开关", &绘制.自瞄.自瞄控件))
					绘制.保存配置();
				if (ImGui::Checkbox("触摸位置", &绘制.自瞄.触摸位置))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("动态自瞄       ", &绘制.自瞄.动态自瞄))
					绘制.保存配置();
				if (ImGui::Checkbox("准星射线", &绘制.自瞄.准星射线))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("倒地不瞄       ", &绘制.自瞄.倒地不瞄))
					绘制.保存配置();
				if (ImGui::Checkbox("人机不瞄", &绘制.自瞄.人机不瞄))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("隐藏自瞄圈", &绘制.自瞄.隐藏自瞄圈))
					绘制.保存配置();
				if (ImGui::Checkbox("掉血自瞄", &绘制.自瞄.掉血自瞄))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("随机触摸点(必点)", &绘制.自瞄.随机触摸点))
					绘制.保存配置();
				if (ImGui::Checkbox("喷子连点", &绘制.自瞄.喷子自瞄))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("停火闪镜", &绘制.自瞄.停火闪镜))
					绘制.保存配置();
				if (ImGui::Checkbox("开火位置", &绘制.自瞄.开火位置))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("连点位置", &绘制.自瞄.连点位置))
					绘制.保存配置();
				if (ImGui::Checkbox("开镜位置", &绘制.自瞄.开镜位置))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("射手自瞄", &绘制.自瞄.射手自瞄))
					绘制.保存配置();
				if (ImGui::Checkbox("框内自瞄", &绘制.自瞄.框内自瞄))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("软锁自瞄", &绘制.自瞄.软锁自瞄))
				{
					if (绘制.自瞄.软锁自瞄)
					{
						绘制.备份.压枪力度 = 绘制.自瞄.压枪力度;
						绘制.备份.自瞄速度 = 绘制.自瞄.自瞄速度;
						绘制.备份.mk20压枪 = 绘制.mk20;

						绘制.自瞄.压枪力度 = 3.5f;
						绘制.自瞄.自瞄速度 = 25.f;
						绘制.mk20 = 0.25f;
					}

					if (!绘制.自瞄.软锁自瞄)
					{
						绘制.自瞄.压枪力度 = 绘制.备份.压枪力度;
						绘制.自瞄.自瞄速度 = 绘制.备份.自瞄速度;
						绘制.mk20 = 绘制.备份.mk20压枪;
					}
				}
				if (ImGui::SliderFloat("触摸范围大小", &绘制.自瞄.触摸范围, 5, 600, "%.0f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("开火范围大小", &绘制.自瞄.开火范围, 5, 600, "%.0f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("连点范围大小", &绘制.自瞄.连点范围, 5, 600, "%.0f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("开镜范围大小", &绘制.自瞄.开镜范围, 5, 600, "%.0f"))
					绘制.保存配置();

				break;
			}
			case 1: {
				if (ImGui::Combo("自瞄条件", &绘制.自瞄.自瞄条件, "开火\0开镜\0开火||开镜\0"))
					绘制.保存配置();
				if (ImGui::Combo("喷子自瞄触发条件", &绘制.自瞄.喷子自瞄条件, "开火\0持续锁定\0"))
					绘制.保存配置();
				if (ImGui::Combo("充电口方向", &绘制.自瞄.充电口方向, "右边\0左边\0"))
					绘制.保存配置();
				if (ImGui::Combo("自瞄优先", &绘制.自瞄.瞄准优先, "准星\0距离\0"))
					绘制.保存配置();
				if (ImGui::Combo("瞄准部位", &绘制.自瞄.瞄准部位, "头部\0胸部\0臀部\0"))
					绘制.保存配置();
				break;
			}
			case 2: {
				ImGui::Text("自瞄调节");
				ImGui::SameLine();
				ImGui::TextDisabled("(?)");
				if (ImGui::BeginItemTooltip())
				{
					ImGui::PushTextWrapPos(ImGui::GetFontSize() * 35.0f);
					ImGui::TextUnformatted(
						"参数调节详细\n"
						"1.自瞄速度 增则慢 减则快\n"
						"2.压枪力度 增则下抬 减则上抬\n");
					ImGui::PopTextWrapPos();
					ImGui::EndTooltip();
				}
				if (ImGui::SliderFloat("自瞄范围", &绘制.自瞄.自瞄范围, 10, 300, "%.0f"))
					绘制.保存配置();

				if (ImGui::SliderFloat("压枪力度", &绘制.自瞄.压枪力度, 0.1, 5, "%.2f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("平滑速度", &绘制.自瞄.自瞄速度, 0.f, 80.f, "%.0f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("掉血自瞄概率", &绘制.自瞄.掉血自瞄数率, 5, 25, "%.0f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("预判速度", &绘制.自瞄.预判力度, 0.0, 2.0, "%.2f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("扫车预判", &绘制.预判度.扫车, 0.0, 4.f, "%.2f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("腰射自瞄距离", &绘制.自瞄.腰射距离限制, 0.0, 300, "%.2f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("开镜自瞄距离", &绘制.自瞄.自瞄距离限制, 0.0, 500, "%.2f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("喷子距离限制", &绘制.自瞄.喷子距离限制, 0, 50, "%.0f"))
					绘制.保存配置();
				if (ImGui::SliderFloat("趴下压枪力度(反)", &绘制.自瞄.趴下位置调节, 0, 2, "%.2f"))
					绘制.保存配置();
				break;
			}
			}
			ImGui::EndChild();
			break;
		}
		case 2: {
			ImGui::BeginChild("##物资", ImVec2(-1, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			static int GoodsMenu = 0;

			ImGui::BeginChild("##物资菜单", ImVec2(-1, 75), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			if (ImGui::Button("枪械"))
				GoodsMenu = 0;
			ImGui::SameLine();
			if (ImGui::Button("子弹"))
				GoodsMenu = 1;
			ImGui::SameLine();
			if (ImGui::Button("头甲包"))
				GoodsMenu = 2;
			ImGui::SameLine();
			if (ImGui::Button("配件"))
				GoodsMenu = 3;
			ImGui::SameLine();
			if (ImGui::Button("药品"))
				GoodsMenu = 4;
			ImGui::SameLine();
			if (ImGui::Button("其他"))
				GoodsMenu = 5;
			ImGui::SameLine();
			if (ImGui::Button("地铁模式"))
				GoodsMenu = 6;
			ImGui::EndChild();

			switch (GoodsMenu)
			{
			case 0: {
				// 步枪 冲锋枪 霰弹枪 狙击枪
				if (ImGui::Checkbox("显示步枪", &绘制.按钮.显示步枪))
					绘制.保存配置();
				if (ImGui::Checkbox("冲锋枪械", &绘制.按钮.冲锋枪械))
					绘制.保存配置();
				if (ImGui::Checkbox("狙击枪械", &绘制.按钮.狙击枪械))
					绘制.保存配置();
				if (ImGui::Checkbox("散弹枪械", &绘制.按钮.散弹枪械))
					绘制.保存配置();
				if (ImGui::Checkbox("爆炸猎弓", &绘制.按钮.爆炸猎弓))
					绘制.保存配置();
				break;
			}
			case 1: {
				// 556 762 9mm 4.5mm 霰弹 信号弹 箭矢
				if (ImGui::Checkbox("5.56", &绘制.按钮.显示556子弹))
					绘制.保存配置();
				if (ImGui::Checkbox("7.62", &绘制.按钮.显示762子弹))
					绘制.保存配置();
				if (ImGui::Checkbox("9毫米", &绘制.按钮.显示9mm子弹))
					绘制.保存配置();
				if (ImGui::Checkbox(".45口径", &绘制.按钮.显示45mm子弹))
					绘制.保存配置();
				if (ImGui::Checkbox("霰弹", &绘制.按钮.显示霰弹))
					绘制.保存配置();
				if (ImGui::Checkbox("信号弹", &绘制.按钮.显示信号弹))
					绘制.保存配置();
				if (ImGui::Checkbox("箭矢", &绘制.按钮.显示箭矢))
					绘制.保存配置();

				break;
			}
			case 2: {
				if (ImGui::Checkbox("三级头", &绘制.按钮.显示三级头))
					绘制.保存配置();
				if (ImGui::Checkbox("三级甲", &绘制.按钮.显示三级甲))
					绘制.保存配置();
				if (ImGui::Checkbox("三级包", &绘制.按钮.显示三级包))
					绘制.保存配置();
				break;
			}
			case 3: {
				if (ImGui::Checkbox("子弹袋", &绘制.按钮.显示子弹袋))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("箭袋", &绘制.按钮.显示箭袋))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("激光瞄准器", &绘制.按钮.显示激光瞄准器))
					绘制.保存配置();

				if (ImGui::Checkbox("轻型握把", &绘制.按钮.显示轻型握把))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("半截握把", &绘制.按钮.显示半截握把))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("UZI枪托", &绘制.按钮.显示UZI枪托))
					绘制.保存配置();

				if (ImGui::Checkbox("狙击枪托", &绘制.按钮.显示狙击枪托))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("步枪枪托", &绘制.按钮.显示步枪枪托))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("狙击枪补偿器", &绘制.按钮.显示狙击枪补偿器))
					绘制.保存配置();

				if (ImGui::Checkbox("狙击枪消焰器", &绘制.按钮.显示狙击枪消焰器))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("狙击枪消音器", &绘制.按钮.显示狙击枪消音器))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("步枪消音器", &绘制.按钮.显示步枪消音器))
					绘制.保存配置();

				if (ImGui::Checkbox("步枪补偿器", &绘制.按钮.显示步枪补偿器))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("步枪消焰器", &绘制.按钮.显示步枪消焰器))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("冲锋枪消音器", &绘制.按钮.显示冲锋枪消音器))
					绘制.保存配置();

				if (ImGui::Checkbox("冲锋枪消焰器", &绘制.按钮.显示冲锋枪消焰器))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("拇指握把", &绘制.按钮.显示拇指握把))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("垂直握把", &绘制.按钮.显示垂直握把))
					绘制.保存配置();

				if (ImGui::Checkbox("直角握把", &绘制.按钮.显示直角握把))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("撞火枪托", &绘制.按钮.显示撞火枪托))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("霰弹快速", &绘制.按钮.显示霰弹快速))
					绘制.保存配置();

				if (ImGui::Checkbox("鸭嘴枪口", &绘制.按钮.显示鸭嘴枪口))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("霰弹收束", &绘制.按钮.显示霰弹收束))
					绘制.保存配置();

				if (ImGui::Checkbox("步枪快扩", &绘制.按钮.步枪快扩))
					绘制.保存配置();
				break;
			}
			case 4: {
				if (ImGui::Checkbox("显示医疗箱", &绘制.按钮.显示医疗箱))
					绘制.保存配置();
				if (ImGui::Checkbox("显示急救包", &绘制.按钮.显示急救包))
					绘制.保存配置();
				if (ImGui::Checkbox("显示绷带", &绘制.按钮.显示绷带))
					绘制.保存配置();
				if (ImGui::Checkbox("显示可乐", &绘制.按钮.显示可乐))
					绘制.保存配置();
				if (ImGui::Checkbox("显示肾上腺素", &绘制.按钮.显示肾上腺素))
					绘制.保存配置();
				if (ImGui::Checkbox("显示止痛药", &绘制.按钮.显示止痛药))
					绘制.保存配置();
				break;
			}
			case 5: {
				if (ImGui::Checkbox("金插", &绘制.按钮.绘制金插))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("召回信号枪", &绘制.按钮.绘制信号枪))
					绘制.保存配置();
				if (ImGui::Checkbox("空投", &绘制.按钮.空投))
					绘制.保存配置();

				break;
			}
			case 6: {
				if (ImGui::Checkbox("宝箱", &绘制.按钮.绘制宝箱))
					绘制.保存配置();
				ImGui::SameLine();
				if (ImGui::Checkbox("武器箱", &绘制.按钮.绘制武器箱))
					绘制.保存配置();
				if (ImGui::Checkbox("超级物资箱", &绘制.按钮.超级物资箱))
					绘制.保存配置();
				if (ImGui::Checkbox("隐藏已开启宝箱、药箱、武器箱", &绘制.按钮.隐藏已开启))
					绘制.保存配置();

				break;
			}
			}
			ImGui::EndChild();
			break;
		}
		case 4: {
			// 压枪
			ImGui::BeginChild("##压枪", ImVec2(-1, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			static int DownWeaponMenu = 0;

			ImGui::BeginChild("##压枪菜单", ImVec2(-1, 75), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			if (ImGui::Button("5.56"))
			{
				DownWeaponMenu = 0;
			}
			ImGui::SameLine();
			if (ImGui::Button("7.62"))
			{
				DownWeaponMenu = 1;
			}
			ImGui::SameLine();
			if (ImGui::Button("冲锋枪"))
			{
				DownWeaponMenu = 2;
			}
			ImGui::SameLine();
			if (ImGui::Button("射手步枪"))
			{
				DownWeaponMenu = 3;
			}
			ImGui::SameLine();
			if (ImGui::Button("握把"))
			{
				DownWeaponMenu = 4;
			}
			ImGui::SameLine();
			ImGui::EndChild();
			switch (DownWeaponMenu)
			{
			case 0: {
				// 556   0.00-3.00
				// M416
				if (ImGui::SliderFloat("M416", &绘制.压枪力.m416, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// scal
				if (ImGui::SliderFloat("scar_l", &绘制.压枪力.scar_l, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// aug
				if (ImGui::SliderFloat("aug", &绘制.压枪力.aug, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// famas
				if (ImGui::SliderFloat("famas", &绘制.压枪力.famas, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// g36c
				if (ImGui::SliderFloat("g36c", &绘制.压枪力.g36c, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// m249
				if (ImGui::SliderFloat("m249", &绘制.压枪力.m249, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();

				break;
			}

			case 1: {
				// 7.62    0.00-3.00
				// akm
				if (ImGui::SliderFloat("akm", &绘制.压枪力.akm, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// m762
				if (ImGui::SliderFloat("m762", &绘制.压枪力.m762, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 蜜獾
				if (ImGui::SliderFloat("蜜獾", &绘制.压枪力.蜜獾, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// pkm
				if (ImGui::SliderFloat("pkm", &绘制.压枪力.pkm, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mg3
				if (ImGui::SliderFloat("mg3", &绘制.压枪力.mg3, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mg-36
				if (ImGui::SliderFloat("mg-36", &绘制.压枪力.mg_36, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				break;
			}
			case 2: {
				// 冲锋枪    0.00-3.00
				// p90
				if (ImGui::SliderFloat("p90", &绘制.压枪力.p90, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// uzi
				if (ImGui::SliderFloat("uzi", &绘制.压枪力.uzi, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// ump45
				if (ImGui::SliderFloat("ump45", &绘制.压枪力.ump45, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// vector
				if (ImGui::SliderFloat("vector", &绘制.压枪力.vector, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 汤姆逊
				if (ImGui::SliderFloat("汤姆逊", &绘制.压枪力.汤姆逊, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 野牛
				if (ImGui::SliderFloat("野牛", &绘制.压枪力.野牛, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();

				break;
			}
			case 3: {
				// 射手步枪    0.00-3.00
				// mini14
				if (ImGui::SliderFloat("mini14", &绘制.压枪力.mini14, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// sks
				if (ImGui::SliderFloat("sks", &绘制.压枪力.sks, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// m417
				if (ImGui::SliderFloat("m417", &绘制.压枪力.m417, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mk20-h
				if (ImGui::SliderFloat("mk20-h", &绘制.压枪力.mk20_h, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mk12
				if (ImGui::SliderFloat("mk12", &绘制.压枪力.mk12, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// vss
				if (ImGui::SliderFloat("vss", &绘制.压枪力.vss, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				break;
			}
			case 4: {
				// 握把
				// 轻型握把
				if (ImGui::SliderFloat("轻型握把", &绘制.轻型压枪力度, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 垂直握把
				if (ImGui::SliderFloat("垂直握把", &绘制.垂直压枪力度, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 拇指握把
				if (ImGui::SliderFloat("拇指握把", &绘制.拇指压枪力度, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 直角握把
				if (ImGui::SliderFloat("直角握把", &绘制.直角压枪力度, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				break;
			}
			}
			ImGui::EndChild();
			break;
		}
		case 5: {
			// 预判
			ImGui::BeginChild("##预判", ImVec2(-1, -1), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			static int PrognosisMenu = 0;

			ImGui::BeginChild("##预判菜单", ImVec2(-1, 75), false, ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize);

			if (ImGui::Button("5.56"))
			{
				PrognosisMenu = 0;
			}
			ImGui::SameLine();
			if (ImGui::Button("7.62"))
			{
				PrognosisMenu = 1;
			}
			ImGui::SameLine();
			if (ImGui::Button("冲锋枪"))
			{
				PrognosisMenu = 2;
			}
			ImGui::SameLine();
			if (ImGui::Button("射手步枪"))
			{
				PrognosisMenu = 3;
			}
			ImGui::SameLine();
			ImGui::EndChild();

			switch (PrognosisMenu)
			{
			case 0: {
				// 556   0.00-3.00
				// M416
				if (ImGui::SliderFloat("M416", &绘制.预判度.m416, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// scal
				if (ImGui::SliderFloat("scar_l", &绘制.预判度.scar_l, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// aug
				if (ImGui::SliderFloat("aug", &绘制.预判度.aug, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// famas
				if (ImGui::SliderFloat("famas", &绘制.预判度.famas, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// g36c
				if (ImGui::SliderFloat("g36c", &绘制.预判度.g36c, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// m249
				if (ImGui::SliderFloat("m249", &绘制.预判度.m249, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				break;
			}

			case 1: {
				// 7.62    0.00-3.00
				// akm
				if (ImGui::SliderFloat("akm", &绘制.预判度.akm, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// m762
				if (ImGui::SliderFloat("m762", &绘制.预判度.m762, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 蜜獾
				if (ImGui::SliderFloat("蜜獾", &绘制.预判度.蜜獾, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// pkm
				if (ImGui::SliderFloat("pkm", &绘制.预判度.pkm, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mg3
				if (ImGui::SliderFloat("mg3", &绘制.预判度.mg3, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mg-36
				if (ImGui::SliderFloat("mg-36", &绘制.预判度.mg_36, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				break;
			}
			case 2: {
				// 冲锋枪    0.00-3.00
				// p90
				if (ImGui::SliderFloat("p90", &绘制.预判度.p90, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// uzi
				if (ImGui::SliderFloat("uzi", &绘制.预判度.uzi, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// ump45
				if (ImGui::SliderFloat("ump45", &绘制.预判度.ump45, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// vector
				if (ImGui::SliderFloat("vector", &绘制.预判度.vector, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 汤姆逊
				if (ImGui::SliderFloat("汤姆逊", &绘制.预判度.汤姆逊, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// 野牛
				if (ImGui::SliderFloat("野牛", &绘制.预判度.野牛, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();

				break;
			}
			case 3: {
				// 射手步枪    0.00-3.00
				// mini14
				if (ImGui::SliderFloat("mini14", &绘制.预判度.mini14, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// sks
				if (ImGui::SliderFloat("sks", &绘制.预判度.sks, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// m417
				if (ImGui::SliderFloat("m417", &绘制.预判度.m417, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mk20-h
				if (ImGui::SliderFloat("mk20-h", &绘制.预判度.mk20_h, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// mk12
				if (ImGui::SliderFloat("mk12", &绘制.预判度.mk12, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				// vss
				if (ImGui::SliderFloat("vss", &绘制.预判度.vss, 0.00f, 5.0f, "%.2f"))
					绘制.保存配置();
				break;
			}
			}

			ImGui::EndChild();

			break;
		}
		}
		if (!悬浮窗)
		{
			悬浮球 = true;
			悬浮窗 = false;
			窗口状态 = true;
			IsBall = false;
			ImGui::SetWindowPos("悬浮图片", 绘制.Pos, ImGuiCond_Always);
		}
		绘制.winWidth = ImGui::GetWindowWidth();
		绘制.winHeith = ImGui::GetWindowHeight();
		g_window = ImGui::GetCurrentWindow();
		ImGui::End();
	}
	drawEnd();
} 

void 布局::开启悬浮窗()
{
	timer WindowDrawing;
	WindowDrawing.SetFps(120);
	WindowDrawing.AotuFPS_init();
	WindowDrawing.setAffinity();
	while (true)
	{
		绘制悬浮窗();
		WindowDrawing.SetFps(绘制.按钮.当前帧率);
		WindowDrawing.AotuFPS();
		std::this_thread::sleep_for(1ms);
	}
}
