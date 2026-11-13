// ==========================================
// FRONTEND JAVASCRIPT
// ==========================================

// DOM ELEMENTS
// Same origin as the page: server.py serves both the UI and the API.
// The server adds our system prompt + request options, so the UI only sends messages.
const API_URL = "/api/chat";
const STATUS_URL = "/monitor/status";
const messageInput = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const messages = document.getElementById("messages");
const welcomeScreen = document.getElementById("welcomeScreen");
const newChatBtn = document.getElementById("newChat");
const chatHistory = document.getElementById("chatHistory");

const themeToggle = document.getElementById("themeToggle");
const attachBtn = document.getElementById("attachBtn");
const fileInput = document.getElementById("fileInput");
const attachmentPreview = document.getElementById("attachmentPreview");

const micBtn = document.getElementById("micBtn");
const voiceStatus = document.getElementById("voiceStatus");
const chatArea = document.getElementById("chatArea");

// STATE
let conversations = JSON.parse(
  localStorage.getItem("nova-chats") || "[]"
);

let currentChat = null;
let selectedFiles = [];
let recognition = null;
let isRecording = false;
let responseTimer = null;


// ==========================================
// SAVE CONVERSATIONS
// ==========================================

function saveChats() {
  localStorage.setItem(
    "nova-chats",
    JSON.stringify(conversations)
  );
}


// ==========================================
// NEW CHAT
// ==========================================

function startNewChat() {
  clearTimeout(responseTimer);

  if (isRecording) {
    stopRecording();
  }

  currentChat = {
    id: Date.now(),
    title: "New Chat",
    messages: []
  };

  conversations.unshift(currentChat);

  selectedFiles = [];

  messageInput.value = "";
  messageInput.style.height = "auto";

  voiceStatus.textContent = "";

  renderAttachments();
  saveChats();
  renderHistory();
  renderMessages();
}


// ==========================================
// CHAT HISTORY
// ==========================================



function renderHistory() {
  chatHistory.innerHTML = "";

  // Pinned chats appear first
const sortedChats = [...conversations].sort(
  (a, b) => Number(b.pinned || false) - Number(a.pinned || false)
);


let lastGroup = null;
let currentGroupContainer = null;

sortedChats.forEach(chat => {
  if (!chat.messages.length) return;

  const currentGroup = chat.pinned ? "Pinned" : "Recent Chats";

  if (currentGroup !== lastGroup) {

    const groupWrapper = document.createElement("div");
    groupWrapper.className = "history-group";

    const heading = document.createElement("button");
    heading.className = "section-label group-toggle";
    heading.type = "button";

    const label = document.createElement("span");
    label.textContent = currentGroup;

    const arrow = document.createElement("span");
    arrow.className = "group-arrow";
    arrow.innerHTML = '<i class="fa-solid fa-chevron-down"></i>';

    heading.append(label, arrow);

    const groupContent = document.createElement("div");
    groupContent.className = "history-group-content";

    heading.addEventListener("click", () => {
        const isCollapsed = groupContent.classList.toggle("collapsed");
        arrow.classList.toggle("rotated", isCollapsed);
    });

    groupWrapper.append(heading, groupContent);
    chatHistory.appendChild(groupWrapper);

    currentGroupContainer = groupContent;
    lastGroup = currentGroup;
}

    const item = document.createElement("div");
    item.className = "history-item";

    if (currentChat && currentChat.id === chat.id) {
      item.classList.add("active");
    }

    // Chat title
    const title = document.createElement("span");
    title.className = "history-title";
    title.textContent = chat.title
    .toLowerCase()
    .replace(/\b\w/g, char => char.toUpperCase());

    // Three-dot menu
    const menuWrapper = document.createElement("div");
    menuWrapper.className = "history-menu-wrapper";

    const menuBtn = document.createElement("button");
    menuBtn.className = "history-menu-btn";
    menuBtn.type = "button";
    menuBtn.title = "Chat options";
    menuBtn.innerHTML = "⋮";

    // Dropdown
    const dropdown = document.createElement("div");
    dropdown.className = "history-dropdown";

    // RENAME CHAT
    const renameOption = document.createElement("button");
    renameOption.className = "dropdown-option";
    renameOption.innerHTML = "✏️ <span>Rename chat</span>";

    renameOption.addEventListener("click", event => {
      event.stopPropagation();

      const newName = prompt(
        "Enter a new name for this chat:",
        chat.title
      );

      if (newName === null) return;

      const trimmedName = newName.trim();

      if (!trimmedName) {
        alert("Chat name cannot be empty.");
        return;
      }

      chat.title = trimmedName;

      saveChats();
      renderHistory();
    });

    // PIN / UNPIN CHAT
    const pinOption = document.createElement("button");
    pinOption.className = "dropdown-option";

    pinOption.innerHTML = chat.pinned
      ? "📌 <span>Unpin chat</span>"
      : "📌 <span>Pin chat</span>";

    pinOption.addEventListener("click", event => {
      event.stopPropagation();

      chat.pinned = !chat.pinned;

      saveChats();
      renderHistory();
    });

    // DELETE CHAT
    const deleteOption = document.createElement("button");
    deleteOption.className = "dropdown-option delete-option";
    deleteOption.innerHTML = "🗑 <span>Delete chat</span>";

    deleteOption.addEventListener("click", event => {
      event.stopPropagation();

      const confirmed = confirm(
        `Delete "${chat.title}"? This cannot be undone.`
      );

      if (!confirmed) return;

      conversations = conversations.filter(
        conversation => conversation.id !== chat.id
      );

      if (currentChat && currentChat.id === chat.id) {
        currentChat = conversations.find(
          conversation => conversation.messages.length > 0
        ) || null;

        if (currentChat) {
          selectedFiles = [];
          renderAttachments();
          renderMessages();
        } else {
          startNewChat();
        }
      }

      saveChats();
      renderHistory();
    });

    // Add options to dropdown
    dropdown.append(
      renameOption,
      pinOption,
      deleteOption
    );

    menuWrapper.append(menuBtn, dropdown);

    // Open / close dropdown
    menuBtn.addEventListener("click", event => {
      event.stopPropagation();

      document.querySelectorAll(".history-dropdown.show")
        .forEach(menu => {
          if (menu !== dropdown) {
            menu.classList.remove("show");
          }
        });

      dropdown.classList.toggle("show");
    });

    // Open conversation
    item.addEventListener("click", () => {
      clearTimeout(responseTimer);

      currentChat = chat;
      selectedFiles = [];

      if (isRecording) {
        stopRecording();
      }

      renderAttachments();
      renderHistory();
      renderMessages();
    });

    item.append(title, menuWrapper);
    currentGroupContainer.appendChild(item);
  });
}

// Close dropdown when clicking outside
document.addEventListener("click", () => {
  document.querySelectorAll(".history-dropdown.show")
    .forEach(menu => menu.classList.remove("show"));
});



// ==========================================
// MARKDOWN RENDERER (small, safe, no libraries)
// Turns **bold**, ### headings, lists, tables, `code` and ``` blocks into HTML.
// All text is HTML-escaped FIRST, so model output can't inject scripts.
// ==========================================

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderMarkdown(text) {
  const lines = escapeHtml(text || "").split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // code block
    if (line.trim().startsWith("```")) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }

    // table: a header row followed by |---|---|
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = l => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => inlineMd(c.trim()));
      let html = "<table><thead><tr>" + cells(line).map(c => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        html += "<tr>" + cells(lines[i]).map(c => `<td>${c}</td>`).join("") + "</tr>";
        i++;
      }
      out.push(`<div class="md-table">${html}</tbody></table></div>`);
      continue;
    }

    // headings
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(h[1].length + 2, 6);   // ### -> h5-ish size, keeps bubbles compact
      out.push(`<h${level}>${inlineMd(h[2])}</h${level}>`);
      i++;
      continue;
    }

    // horizontal rule
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    // lists (bullets or numbers)
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items = [];
      while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          items.push(inlineMd(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "")));
        } else {
          items[items.length - 1] += "<br>" + inlineMd(lines[i].trim());   // indented continuation line
        }
        i++;
      }
      const tag = ordered ? "ol" : "ul";
      const start = ordered ? ` start="${parseInt(line.trim(), 10) || 1}"` : "";   // keep "2." as 2
      out.push(`<${tag}${start}>${items.map(x => `<li>${x}</li>`).join("")}</${tag}>`);
      continue;
    }

    // blank line
    if (!line.trim()) { i++; continue; }

    // paragraph: join consecutive plain lines
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(#{1,6}\s|```|([-*+]|\d+[.)])\s)/.test(lines[i]) &&
           !(lines[i].includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1]))) {
      para.push(inlineMd(lines[i]));
      i++;
    }
    out.push(`<p>${para.join("<br>")}</p>`);
  }
  return out.join("");
}


// ==========================================
// DISPLAY MESSAGES
// ==========================================

function renderMessages() {
  messages.innerHTML = "";

  const hasMessages =
    currentChat && currentChat.messages.length > 0;

  welcomeScreen.style.display =
    hasMessages ? "none" : "block";

  if (!currentChat) return;

  currentChat.messages.forEach(msg => {

    const wrapper = document.createElement("div");

    wrapper.className = `message ${msg.role}`;

    const bubble = document.createElement("div");

    bubble.className = "message-bubble";
    if (msg.role === "assistant") {
      bubble.classList.add("md");
      bubble.innerHTML = renderMarkdown(msg.text);   // escaped first, so it's safe
    } else {
      bubble.textContent = msg.text;
    }

    wrapper.appendChild(bubble);

    // DISPLAY ATTACHMENTS

    if (msg.attachments && msg.attachments.length) {

      const files = document.createElement("div");

      files.className = "message-attachments";

      msg.attachments.forEach(file => {

        const chip = document.createElement("div");

        chip.className = "message-attachment";
        chip.textContent = "📎 " + file;

        files.appendChild(chip);

      });

      wrapper.appendChild(files);
    }

    messages.appendChild(wrapper);

  });

  chatArea.scrollTop = chatArea.scrollHeight;
}


// ==========================================
// FILE ATTACHMENTS
// ==========================================

attachBtn.addEventListener("click", () => {
  fileInput.click();
});


fileInput.addEventListener("change", () => {

  const incoming = Array.from(fileInput.files);

  incoming.forEach(file => {

    const alreadyAdded = selectedFiles.some(
      existing =>
        existing.name === file.name &&
        existing.size === file.size &&
        existing.lastModified === file.lastModified
    );

    if (alreadyAdded) return;

    if (selectedFiles.length >= 5) {
      alert("You can attach a maximum of 5 files.");
      return;
    }

    selectedFiles.push(file);

  });

  renderAttachments();

  fileInput.value = "";

});


function renderAttachments() {

  attachmentPreview.innerHTML = "";

  selectedFiles.forEach((file, index) => {

    const chip = document.createElement("div");

    chip.className = "attachment-chip";

    const icon = document.createElement("i");

    icon.className = file.type.startsWith("image/")
      ? "fa-regular fa-image"
      : "fa-solid fa-file-lines";

    const name = document.createElement("span");

    name.textContent = file.name;

    const remove = document.createElement("button");

    remove.type = "button";
    remove.title = "Remove attachment";

    remove.innerHTML =
      '<i class="fa-solid fa-xmark"></i>';

    remove.addEventListener("click", () => {

      selectedFiles.splice(index, 1);

      renderAttachments();

    });

    chip.append(icon, name, remove);

    attachmentPreview.appendChild(chip);

  });

}


// ==========================================
// SEND MESSAGE
// ==========================================

function sendMessage() {

  const text = messageInput.value.trim();

  if (!text && selectedFiles.length === 0) return;

  if (isRecording) {
    stopRecording();
  }

  if (!currentChat) {
    startNewChat();
  }

  const attachments = selectedFiles.map(
    file => file.name
  );

  currentChat.messages.push({

    role: "user",

    text: text || "Please see my attached file.",

    attachments

  });

  if (currentChat.messages.length === 1) {

    currentChat.title = text
      ? text.slice(0, 35)
      : attachments[0] || "File attachment";

  }

  selectedFiles = [];

  renderAttachments();

  messageInput.value = "";
  messageInput.style.height = "auto";

  renderMessages();
  renderHistory();
  saveChats();
  
  // ==========================================
  // LOCAL GREETING SYSTEM
  // ==========================================

  const greetingPattern =
    /^(hi|hello|hey|good morning|good afternoon|good evening|howdy)(\s+there)?[!.?]*$/i;

  if (greetingPattern.test(text) && attachments.length === 0) {

    const greetingReply =
      "Hello! 👋 Welcome to Silent Shift. I'm here and ready to help. What would you like to explore today?";

    currentChat.messages.push({
      role: "assistant",
      text: greetingReply
    });

    renderMessages();
    renderHistory();
    saveChats();

    return;
  }

  
  // ==========================================
  // REAL AI BACKEND CONNECTION
  // ==========================================

  clearTimeout(responseTimer);

  const chatToUpdate = currentChat;

  const typing = document.createElement("div");
  typing.className = "typing";
  typing.textContent = "Silent Shift is thinking...";

  messages.appendChild(typing);

  chatArea.scrollTop = chatArea.scrollHeight;

  // Send the conversation history to Ollama
  const requestMessages = chatToUpdate.messages.map(msg => ({
    role: msg.role,
    content: msg.text
  }));

  (async () => {
    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          messages: requestMessages
        })
      });

      if (!response.ok) {
        let detail = "";
        try { detail = (await response.json()).detail || ""; } catch (e) {}
        throw new Error(`Backend error ${response.status} ${detail}`);
      }

      const data = await response.json();

      const reply = data.message?.content;

      if (!reply) {
        throw new Error("The backend returned an empty response.");
      }

      chatToUpdate.messages.push({
        role: "assistant",
        text: reply
      });

    } catch (error) {
      console.error("AI Backend Error:", error);

      chatToUpdate.messages.push({
        role: "assistant",
        text: "I'm having trouble connecting to the AI backend (" + error.message + "). Is `python run.py` running?"
      });

    } finally {
      typing.remove();

      saveChats();
      renderMessages();
      renderHistory();
    }
  })();

}
// SEND BUTTON

sendBtn.addEventListener("click", sendMessage);


// ENTER TO SEND

messageInput.addEventListener("keydown", event => {

  if (
    event.key === "Enter" &&
    !event.shiftKey
  ) {

    event.preventDefault();

    sendMessage();

  }

});


// AUTO-RESIZE TEXTAREA

messageInput.addEventListener("input", () => {

  messageInput.style.height = "auto";

  messageInput.style.height =
    Math.min(messageInput.scrollHeight, 140) + "px";

});


// ==========================================
// VOICE INPUT - SPEECH TO TEXT
// ==========================================

const SpeechRecognition =
  window.SpeechRecognition ||
  window.webkitSpeechRecognition;

if (SpeechRecognition) {

  recognition = new SpeechRecognition();

  recognition.lang = "en-IN";

  recognition.continuous = false;

  recognition.interimResults = false;

  recognition.maxAlternatives = 1;


  // RECORDING STARTED

 recognition.onresult = (event) => {

  const transcript =
    event.results[0][0].transcript.trim();

  if (transcript) {
    messageInput.value = transcript;

    messageInput.dispatchEvent(
      new Event("input")
    );

    messageInput.focus();
  }

};

  // CAPTURE COMPLETED SPEECH

  recognition.onresult = event => {

    const transcript =
      event.results[0][0].transcript.trim();

    if (transcript) {

      const existingText = messageInput.value.trim();

      messageInput.value = existingText
        ? existingText + " " + transcript
        : transcript;

      // Update textarea height

      messageInput.dispatchEvent(
        new Event("input")
      );

      voiceStatus.textContent =
        "Voice converted to text ✓";

      messageInput.focus();

    }

  };


  // ERROR HANDLING

  recognition.onerror = event => {

    const errors = {

      "not-allowed": "Microphone permission denied.",

      "no-speech": "No speech detected. Try again.",

      "network": "Speech recognition network error.",

      "audio-capture": "Microphone not detected."

    };

    voiceStatus.textContent =
      errors[event.error] || "Voice recognition failed.";

    stopRecording();

  };


  // RECORDING FINISHED

  recognition.onend = () => {

    isRecording = false;

    micBtn.classList.remove("recording");

    micBtn.innerHTML =
      '<i class="fa-solid fa-microphone"></i>';

    if (voiceStatus.textContent === "Listening...") {

      voiceStatus.textContent =
        "Voice converted to text ✓";

    }

  };

} else {

  micBtn.title =
    "Speech recognition is not supported in this browser.";

}


// STOP RECORDING

function stopRecording() {

  if (recognition && isRecording) {

    recognition.stop();

  }

  isRecording = false;

  micBtn.classList.remove("recording");

  micBtn.innerHTML =
    '<i class="fa-solid fa-microphone"></i>';

}


// MICROPHONE BUTTON

micBtn.addEventListener("click", () => {

  if (!recognition) {

    alert(
      "Speech recognition is not supported. Please try Chrome or Edge."
    );

    return;

  }

  if (isRecording) {

    stopRecording();

    return;

  }

  voiceStatus.textContent = "";

  try {

    recognition.start();

  } catch (error) {

    voiceStatus.textContent =
      "Unable to start microphone. Please try again.";

  }

});


// ==========================================
// DARK / LIGHT MODE
// ==========================================

function applyTheme(theme) {

  document.body.classList.toggle(
    "light",
    theme === "light"
  );

  themeToggle.innerHTML = theme === "dark"

    ? '<i class="fa-solid fa-sun"></i><span>Switch to light mode</span>'

    : '<i class="fa-solid fa-moon"></i><span>Switch to dark mode</span>';

}


const savedTheme =
  localStorage.getItem("nova-theme") || "dark";

applyTheme(savedTheme);


themeToggle.addEventListener("click", () => {

  const nextTheme =
    document.body.classList.contains("light")
      ? "dark"
      : "light";

  localStorage.setItem("nova-theme", nextTheme);

  applyTheme(nextTheme);

});


// ==========================================
// MOBILE SIDEBAR
// ==========================================

document.getElementById("menuToggle")
  .addEventListener("click", () => {

    document.getElementById("sidebar")
      .classList.toggle("open");

  });


// ==========================================
// NEW CHAT BUTTON
// ==========================================

newChatBtn.addEventListener(
  "click",
  startNewChat
);


// ==========================================
// INITIALIZE NOVA
// ==========================================

if (conversations.length > 0) {

  currentChat = conversations[0];

  renderMessages();

  renderHistory();

} else {

  startNewChat();

}


// ==========================================
// MODEL HEALTH (from the SilentShift monitor)
// ==========================================

const healthPill = document.getElementById("healthPill");
const healthDot = document.getElementById("healthDot");
const healthText = document.getElementById("healthText");

async function refreshHealth() {
  try {
    const r = await fetch(STATUS_URL);
    const s = await r.json();
    let cls = "ok", text = "bot-latest · behaving normally";
    if (!s.baseline) {
      cls = "idle"; text = "bot-latest · monitor has no baseline yet";
    } else if (s.job && s.job.state === "running") {
      cls = "busy"; text = `bot-latest · monitor running ${s.job.kind}…`;
    } else if (s.latest && s.latest.changed) {
      cls = "alert";
      const cause = (s.latest.likely_cause || "unknown").replace("_", " ");
      text = `bot-latest · behaviour changed (likely ${cause})`;
    } else if (!s.latest) {
      cls = "idle"; text = "bot-latest · baseline learned, no check yet";
    }
    healthPill.className = "topbar-brand health-pill " + cls;
    healthText.textContent = text;
  } catch (e) {
    healthPill.className = "topbar-brand health-pill alert";
    healthText.textContent = "monitor offline";
  }
}
refreshHealth();
setInterval(refreshHealth, 5000);

document.querySelectorAll(".suggestion").forEach(btn => {
  btn.addEventListener("click", () => {
    messageInput.value = btn.dataset.prompt;
    sendMessage();
  });
});