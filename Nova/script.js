
// ==========================================
// FRONTEND JAVASCRIPT
// ==========================================

// DOM ELEMENTS
const API_URL ="https://str-cluster-scenic-temperatures.trycloudflare.com/api/chat";
const API_MODEL = "bot-latest";
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
    bubble.textContent = msg.text;

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
          model: API_MODEL,
          messages: requestMessages,
          stream: false
        })
      });

      if (!response.ok) {
        throw new Error(
          `Backend error: ${response.status}`
        );
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
        text: "I'm having trouble connecting to the AI backend. Please try again in a moment."
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
