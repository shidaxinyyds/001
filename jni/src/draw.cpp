#include "辅助类.h"
#include "Font.h"

extern 绘制 绘制;

// Var
EGLDisplay display = EGL_NO_DISPLAY;
EGLConfig config;
EGLSurface surface = EGL_NO_SURFACE;
EGLContext context = EGL_NO_CONTEXT;

ANativeWindow* native_window;
//
int native_window_screen_x = 0;
int native_window_screen_y = 0;
android::ANativeWindowCreator::DisplayInfo displayInfo{ 0 };
uint32_t orientation = 0;
bool g_Initialized = false;
ImGuiWindow* g_window = nullptr;
int Orientation;

// JNI 提供的外部窗口和屏幕信息（免root方案）
static ANativeWindow* g_external_window = nullptr;
static int g_java_screen_w = 0;
static int g_java_screen_h = 0;
static int g_java_orientation = 0;

void set_external_window(ANativeWindow* window)
{
	g_external_window = window;
}

void set_screen_info(int w, int h, int orient)
{
	g_java_screen_w = w;
	g_java_screen_h = h;
	g_java_orientation = orient;
}

bool initGUI_draw(uint32_t _screen_x, uint32_t _screen_y, bool log)
{
	orientation = displayInfo.orientation;

	if (!init_egl(_screen_x, _screen_y, log))
	{
		return false;
	}
	if (!ImGui_init())
	{
		return false;
	}
	//UploadFonts();
	return true;
}

bool init_egl(uint32_t _screen_x, uint32_t _screen_y, bool log)
{
	if (g_external_window)
	{
		// 免root方案：使用 Java WindowManager 创建的 Surface
		::native_window = g_external_window;
	}
	else
	{
		// 原 root 方案：通过 SurfaceComposer 创建窗口
		string strbool = "1";
		if (strbool == "y" || strbool == "1" || strbool == "Y")
		{
			::native_window = android::ANativeWindowCreator::Create("AImGui", _screen_x, _screen_y, true);
		}
		else
		{
			::native_window = android::ANativeWindowCreator::Create("AImGui", _screen_x, _screen_y);
		}
	}

	ANativeWindow_acquire(native_window);
	display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
	if (display == EGL_NO_DISPLAY)
	{
		return false;
	}
	if (eglInitialize(display, 0, 0) != EGL_TRUE)
	{
		return false;
	}
	EGLint num_config = 0;
	const EGLint attribList[] = {
			EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
			EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
			EGL_BLUE_SIZE, 5,   //-->delete
			EGL_GREEN_SIZE, 6,  //-->delete
			EGL_RED_SIZE, 5,    //-->delete
			EGL_BUFFER_SIZE, 32,  //-->new field
			EGL_DEPTH_SIZE, 16,
			EGL_STENCIL_SIZE, 8,
			EGL_NONE
	};
	const EGLint attrib_list[] = {
			EGL_CONTEXT_CLIENT_VERSION,
			3,
			EGL_NONE
	};

	if (eglChooseConfig(display, attribList, &config, 1, &num_config) != EGL_TRUE)
	{
		return false;
	}
	EGLint egl_format;
	eglGetConfigAttrib(display, config, EGL_NATIVE_VISUAL_ID, &egl_format);
	ANativeWindow_setBuffersGeometry(native_window, 0, 0, egl_format);
	context = eglCreateContext(display, config, EGL_NO_CONTEXT, attrib_list);
	if (context == EGL_NO_CONTEXT)
	{
		return false;
	}
	surface = eglCreateWindowSurface(display, config, native_window, nullptr);
	if (surface == EGL_NO_SURFACE)
	{
		return false;
	}
	if (!eglMakeCurrent(display, surface, surface, context))
	{
		return false;
	}
	return true;
}

void screen_config()
{
	if (g_java_screen_w > 0)
	{
		// 免root方案：使用 Java 传入的屏幕参数
		displayInfo.width = g_java_screen_w;
		displayInfo.height = g_java_screen_h;
		displayInfo.orientation = g_java_orientation;
	}
	else
	{
		// 原 root 方案：通过 SurfaceComposer 获取屏幕信息
		displayInfo = android::ANativeWindowCreator::GetDisplayInfo();
	}
	Orientation = displayInfo.orientation;
}

bool loadSystemFont()
{
	// 定义系统字体文件路径
	const char* systemFontPath = "/system/fonts/NotoSansCJK-Regular.ttc";

	// 打开系统字体文件
	int fd = open(systemFontPath, O_RDONLY);
	if (fd == -1)
	{
		return false;
	}

	// 获取系统字体文件的大小
	struct stat fileStat;
	if (fstat(fd, &fileStat) == -1)
	{
		close(fd);
		return false;
	}
	off_t fileSize = fileStat.st_size;

	// 映射系统字体文件到内存
	void* fileData = mmap(NULL, fileSize, PROT_READ, MAP_PRIVATE, fd, 0);
	if (fileData == MAP_FAILED)
	{
		close(fd);
		return false;
	}

	// 加载系统字体文件
	ImGuiIO& io = ImGui::GetIO();

	ImFontConfig fontCfg;
	fontCfg.FontDataOwnedByAtlas = false;
	io.Fonts->AddFontFromMemoryTTF(fileData, fileSize, 32.0f, &fontCfg, io.Fonts->GetGlyphRangesChineseFull());

	// 解除内存映射和关闭文件
	munmap(fileData, fileSize);
	close(fd);

	return true;
}

bool ImGui_init()
{
	if (g_Initialized)
	{
		return true;
	}
	/*
	if (绘制.Cloudcheck() == 0) {
	  return false;
	}
	*/
	IMGUI_CHECKVERSION();
	ImGui::CreateContext();
	ImGui::StyleColorsLight();
	ImGui_ImplAndroid_Init(native_window);
	ImGui_ImplOpenGL3_Init("#version 300 es");
	ImGuiIO& io = ImGui::GetIO();
	io.IniFilename = NULL;
	io.Fonts->AddFontFromMemoryTTF((void*)OPPOSans_H, OPPOSans_H_size, 39.f, NULL, io.Fonts->GetGlyphRangesChineseFull());
	// 加载系统字体
	/*
	bool success = loadSystemFont();
	if (!success) {
		// 处理加载字体失败的情况
		return false;
	}
	*/
	// 顶部显示时间那一栏颜色
	ImGuiStyle& Style = ImGui::GetStyle();
	Style.FrameRounding = 5.0f;
	/* Style.Colors[ImGuiCol_TitleBg] = ImVec4(0.0f, 0.0f, 0.00f, 1.f);
	Style.Colors[ImGuiCol_TitleBgActive] = ImVec4(0.0f, 0.0f, 0.0f, 1.f);
	//     Style.Colors[ImGuiCol_TitleBgCollapsed]       = ImVec4(0.00f, 0.00f, 0.00f, 0.2f);

	//     //窗口背景颜色
	Style.Colors[ImGuiCol_WindowBg] = ImVec4(0.0f, 0.0f, 0.0f, 1.f);

	//      //字体颜色
	Style.Colors[ImGuiCol_Text] = ImVec4(1.0f, 1.0f, 1.0f, 1.0f);

	//    //功能按钮颜色
	Style.Colors[ImGuiCol_Button] = ImVec4(0.0f, 0.0f, 0.00f, 0.0f);       // 按钮没点击时粉色，透明度0.7
	Style.Colors[ImGuiCol_ButtonHovered] = ImVec4(0.0f, 1.0f, 0.0f, 1.0f); // 按钮选中时颜色
	Style.Colors[ImGuiCol_ButtonActive] = ImVec4(0.0f, 0.0f, 0.00f, 0.0f); // 按钮点击时颜色，和雷达颜色同步

	//     //窗口分类按钮颜色
	Style.Colors[ImGuiCol_Tab] = ImVec4(0.0f, 0.0f, 0.00f, 0.0f);       // 普通状态
	Style.Colors[ImGuiCol_TabHovered] = ImVec4(0.0f, 1.0f, 0.0f, 1.0f); // 按钮选中时颜色
	Style.Colors[ImGuiCol_TabActive] = ImVec4(0.0f, 0.0f, 0.00f, 0.0f); // 点击时颜色

	//     //雷达颜色
	Style.Colors[ImGuiCol_Header] = ImVec4(0.0f, 0.0f, 0.00f, 0.0f);       // 普通状态
	Style.Colors[ImGuiCol_HeaderHovered] = ImVec4(0.0f, 1.0f, 0.0f, 1.0f); // 选中时颜色
	Style.Colors[ImGuiCol_HeaderActive] = ImVec4(0.0f, 0.0f, 0.00f, 0.0f); // 点击时颜色

	Style.Colors[ImGuiCol_CheckMark] = ImVec4(0.0f, 1.0f, 0.0f, 1.0f); // 打勾后的颜色

	//     // 滑动条颜色
	Style.Colors[ImGuiCol_SliderGrab] = ImVec4(0.0f, 1.0f, 0.0f, 1.0f); // 滑动条滑块颜色
	//      Style.Colors[ImGuiCol_SliderGrabActive]        = ImVec4(1.0f, 0.4f, 0.7f, 1.0f); // 滑动条滑块按下颜色 */

	ImGui::GetStyle().ScaleAllSizes(3.0f);
	ImGui::GetStyle().ScrollbarSize = 30.0f; // ui滑动条粗细 
	::g_Initialized = true;
	return true;
}

int 布局::初始化程序()
{
	// 获取屏幕信息
	screen_config();
	::abs_ScreenX = (displayInfo.height > displayInfo.width ? displayInfo.height : displayInfo.width);
	::abs_ScreenY = (displayInfo.height < displayInfo.width ? displayInfo.height : displayInfo.width);

	::native_window_screen_x = (displayInfo.height > displayInfo.width ? displayInfo.height : displayInfo.width);
	::native_window_screen_y = (displayInfo.height > displayInfo.width ? displayInfo.height : displayInfo.width);
	// 初始化imgui
	if (!initGUI_draw(native_window_screen_x, native_window_screen_y, true))
	{
		return -1;
	}
	Touch_Init(displayInfo.width, displayInfo.height, displayInfo.orientation, false);
	return 0;
}

void drawBegin()
{
	screen_config();

	if (::orientation != displayInfo.orientation)
	{
		::orientation = displayInfo.orientation;
		UpdateScreenData(displayInfo.width, displayInfo.height, displayInfo.orientation);
		// g_window->Pos.x = 100;
		// g_window->Pos.y = 125;
	}

	// ImGui_ImplVulkan_NewFrame();
	ImGui_ImplOpenGL3_NewFrame();
	ImGui_ImplAndroid_NewFrame(native_window_screen_x, native_window_screen_y);
	ImGui::NewFrame();
}

void drawEnd()
{
	ImGui::Render();

	glClear(GL_COLOR_BUFFER_BIT);
	ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
	eglSwapBuffers(display, surface);
	/*
	FrameRender(ImGui::GetDrawData());
	FramePresent();
	*/
}

/*
void shutdown() {
	if (!g_Initialized) {
		return;
	}
	DeviceWait();
	ImGui_ImplVulkan_Shutdown();
	ImGui_ImplAndroid_Shutdown();
	ImGui::DestroyContext();
	CleanupVulkanWindow();
	CleanupVulkan();

	if (native_window != nullptr) {
		ANativeWindow_release(native_window);
		android::ANativeWindowCreator::Destroy(native_window);
		native_window = nullptr;
	}

	g_Initialized = false;
}
*/