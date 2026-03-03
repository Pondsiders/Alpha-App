import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useState, useCallback, useRef } from "react";
import ChatPage from "./pages/ChatPage";
import DevContextMeter from "./pages/DevContextMeter";
import DevStatusBar from "./pages/DevStatusBar";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/AppSidebar";

function Layout() {
  const [chatKey, setChatKey] = useState(0);
  const refreshRef = useRef<(() => void) | null>(null);

  const handleChatKey = useCallback(() => {
    setChatKey((k) => k + 1);
  }, []);

  const handleSessionCreated = useCallback(() => {
    refreshRef.current?.();
  }, []);

  return (
    <SidebarProvider>
      <AppSidebar onChatKey={handleChatKey} refreshRef={refreshRef} />
      <main className="flex-1 flex flex-col min-w-0 h-svh">
        <ChatPage key={chatKey} onSessionCreated={handleSessionCreated} />
      </main>
    </SidebarProvider>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />} />
        <Route path="/chat" element={<Layout />} />
        <Route path="/chat/:sessionId" element={<Layout />} />
        <Route path="/dev/context-meter" element={<DevContextMeter />} />
        <Route path="/dev/status-bar" element={<DevStatusBar />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
