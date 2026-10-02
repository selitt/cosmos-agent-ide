#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

@interface Cosmos : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate>
@property NSWindow *window;
@property WKWebView *web;
@property NSTask *server;
@property NSPipe *pipe;
@property NSMutableString *buffer;
@property NSURL *url;
@end

@implementation Cosmos
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    NSMenu *menu = [NSMenu new];
    NSMenu *appMenu = [NSMenu new];
    NSMenuItem *appItem = [NSMenuItem new]; appItem.submenu = appMenu; [menu addItem:appItem];
    [appMenu addItemWithTitle:@"О Cosmos" action:@selector(about:) keyEquivalent:@""];
    [appMenu addItem:[NSMenuItem separatorItem]];
    [appMenu addItemWithTitle:@"Завершить Cosmos" action:@selector(terminate:) keyEquivalent:@"q"];
    NSMenu *fileMenu = [[NSMenu alloc] initWithTitle:@"Файл"];
    NSMenuItem *fileItem = [[NSMenuItem alloc] initWithTitle:@"Файл" action:nil keyEquivalent:@""]; fileItem.submenu = fileMenu; [menu addItem:fileItem];
    [fileMenu addItemWithTitle:@"Открыть папку…" action:@selector(chooseFolder:) keyEquivalent:@"o"];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Правка"];
    NSMenuItem *editItem = [[NSMenuItem alloc] initWithTitle:@"Правка" action:nil keyEquivalent:@""]; editItem.submenu = editMenu; [menu addItem:editItem];
    [editMenu addItemWithTitle:@"Отменить" action:@selector(undo:) keyEquivalent:@"z"];
    [editMenu addItemWithTitle:@"Вырезать" action:@selector(cut:) keyEquivalent:@"x"];
    [editMenu addItemWithTitle:@"Копировать" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"Вставить" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"Выделить всё" action:@selector(selectAll:) keyEquivalent:@"a"];
    NSApp.mainMenu = menu;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1380, 880) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Cosmos Agent IDE";
    self.window.minSize = NSMakeSize(980, 650);
    self.window.delegate = self;
    self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    WKWebViewConfiguration *configuration = [WKWebViewConfiguration new];
    configuration.websiteDataStore = [WKWebsiteDataStore nonPersistentDataStore];
    self.web = [[WKWebView alloc] initWithFrame:self.window.contentView.bounds configuration:configuration];
    self.web.autoresizingMask = NSViewWidthSizable|NSViewHeightSizable;
    self.web.navigationDelegate = self; self.web.UIDelegate = self;
    [self.window.contentView addSubview:self.web];
    [self.window center]; [self.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES];
    NSString *last = [[NSUserDefaults standardUserDefaults] stringForKey:@"workspace"];
    if (last && [[NSFileManager defaultManager] fileExistsAtPath:last]) [self startServer:last];
    else [self chooseFolder:nil];
}
- (void)about:(id)sender { [self showMessage:@"Cosmos Agent IDE 0.1.0\nPython + Codex + Gemini\nКоманда AI-агентов в одном пространстве."]; }
- (void)showMessage:(NSString *)message { NSAlert *alert = [NSAlert new]; alert.messageText = @"Cosmos"; alert.informativeText = message; [alert runModal]; }
- (void)chooseFolder:(id)sender {
    if (self.server) {
        NSAlert *alert = [NSAlert new]; alert.messageText = @"Открыть другую папку?";
        alert.informativeText = @"Сохраните файлы. Текущие процессы и чаты будут закрыты.";
        [alert addButtonWithTitle:@"Открыть папку"]; [alert addButtonWithTitle:@"Отмена"];
        if ([alert runModal] != NSAlertFirstButtonReturn) return;
    }
    NSOpenPanel *panel = [NSOpenPanel openPanel]; panel.canChooseDirectories = YES; panel.canChooseFiles = NO; panel.allowsMultipleSelection = NO;
    panel.prompt = @"Открыть проект"; panel.message = @"Выберите рабочую папку для Python и AI-агентов";
    if ([panel runModal] == NSModalResponseOK) [self startServer:panel.URL.path];
}
- (void)startServer:(NSString *)root {
    [self stopServer];
    NSString *resource = [[NSBundle mainBundle] resourcePath];
    NSArray *candidates = @[[NSProcessInfo processInfo].environment[@"COSMOS_PYTHON"] ?: @"", @"/usr/local/bin/python3", @"/opt/homebrew/bin/python3", @"/Library/Frameworks/Python.framework/Versions/3.12/bin/python3", @"/usr/bin/python3"];
    NSString *python = nil;
    for (NSString *candidate in candidates) if ([[NSFileManager defaultManager] isExecutableFileAtPath:candidate]) { python = candidate; break; }
    if (!python) { [self showMessage:@"Установите Python 3.10+ с python.org и откройте Cosmos снова."]; return; }
    NSTask *task = [NSTask new]; NSPipe *pipe = [NSPipe pipe];
    task.executableURL = [NSURL fileURLWithPath:python];
    task.arguments = @[[resource stringByAppendingPathComponent:@"server.py"], root, @"--port", @"0", @"--no-browser"];
    NSMutableDictionary *environment = [[NSProcessInfo processInfo].environment mutableCopy];
    environment[@"PATH"] = [@"/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:" stringByAppendingString:environment[@"PATH"] ?: @""];
    task.environment = environment; task.standardOutput = pipe; task.standardError = pipe;
    self.buffer = [NSMutableString new]; self.url = nil;
    __weak Cosmos *weakSelf = self;
    pipe.fileHandleForReading.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *data = handle.availableData;
        if (!data.length) return;
        NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        if (!text) return;
        dispatch_async(dispatch_get_main_queue(), ^{
            Cosmos *strongSelf = weakSelf;
            if (!strongSelf || strongSelf.server != task) return;
            [strongSelf.buffer appendString:text];
            NSRange start = [strongSelf.buffer rangeOfString:@"http://127.0.0.1:"];
            if (!strongSelf.url && start.location != NSNotFound) {
                NSString *line = [[[strongSelf.buffer substringFromIndex:start.location] componentsSeparatedByString:@"\n"] firstObject];
                if ([strongSelf.buffer hasSuffix:@"\n"]) {
                    strongSelf.url = [NSURL URLWithString:line];
                    [strongSelf.web loadRequest:[NSURLRequest requestWithURL:strongSelf.url]];
                }
            }
        });
    };
    task.terminationHandler = ^(NSTask *finished) {
        dispatch_async(dispatch_get_main_queue(), ^{
            Cosmos *strongSelf = weakSelf;
            if (strongSelf.server == finished && finished.terminationStatus != 0)
                [strongSelf showMessage:[@"Не удалось запустить сервер.\n" stringByAppendingString:strongSelf.buffer ?: @""]];
        });
    };
    self.server = task; self.pipe = pipe;
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) [self showMessage:error.localizedDescription];
    else { [[NSUserDefaults standardUserDefaults] setObject:root forKey:@"workspace"]; self.window.title = [@"Cosmos — " stringByAppendingString:root.lastPathComponent]; }
}
- (void)stopServer {
    self.pipe.fileHandleForReading.readabilityHandler = nil;
    NSTask *task = self.server; self.server = nil;
    if (task.running) { [task terminate]; [task waitUntilExit]; }
    self.pipe = nil;
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = action.request.URL;
    if ([url.host isEqualToString:@"127.0.0.1"] && [url.port isEqual:self.url.port]) decisionHandler(WKNavigationActionPolicyAllow);
    else { if ([url.scheme isEqualToString:@"https"]) [[NSWorkspace sharedWorkspace] openURL:url]; decisionHandler(WKNavigationActionPolicyCancel); }
}
- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
    if ([action.request.URL.scheme isEqualToString:@"https"]) [[NSWorkspace sharedWorkspace] openURL:action.request.URL]; return nil;
}
- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))completionHandler {
    NSAlert *alert = [NSAlert new]; alert.messageText = message; [alert addButtonWithTitle:@"Да"]; [alert addButtonWithTitle:@"Отмена"];
    completionHandler([alert runModal] == NSAlertFirstButtonReturn);
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    NSAlert *alert = [NSAlert new]; alert.messageText = @"Закрыть Cosmos?"; alert.informativeText = @"Убедитесь, что файлы сохранены. Работающие процессы будут остановлены.";
    [alert addButtonWithTitle:@"Закрыть"]; [alert addButtonWithTitle:@"Отмена"];
    return [alert runModal] == NSAlertFirstButtonReturn ? NSTerminateNow : NSTerminateCancel;
}
- (void)applicationWillTerminate:(NSNotification *)notification { [self stopServer]; }
- (BOOL)windowShouldClose:(NSWindow *)sender { [NSApp terminate:nil]; return NO; }
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool { NSApplication *app = [NSApplication sharedApplication]; Cosmos *delegate = [Cosmos new]; app.delegate = delegate; [app run]; }
    return 0;
}
