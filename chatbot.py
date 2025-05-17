import streamlit as st
import uuid
import requests
import msal
import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from langdetect import detect

# Get Microsoft Entra External ID credentials from Key Vault
try:
    credential = DefaultAzureCredential()
    key_vault_name = os.environ.get("KEY_VAULT_NAME")
    key_vault_url = f"https://{key_vault_name}.vault.azure.net"
    secret_client = SecretClient(vault_url=key_vault_url, credential=credential)

    # Entra External ID configuration
    tenant_name = secret_client.get_secret("PROJ-ENTRA-TENANT-NAME").value
    client_id = secret_client.get_secret("PROJ-ENTRA-CLIENT-ID").value
    policy_id = secret_client.get_secret("PROJ-ENTRA-POLICY-ID").value
    authority_domain = secret_client.get_secret("PROJ-ENTRA-AUTHORITY-DOMAIN").value
    
    # Check if Arabic support is enabled
    support_arabic = os.environ.get("SUPPORT_ARABIC", "false").lower() == "true"
except Exception as e:
    st.error(f"Error loading configuration: {str(e)}")
    tenant_name = ""
    client_id = ""
    policy_id = ""
    authority_domain = "login.microsoftonline.com"
    support_arabic = False

# Backend URLs define
BACKEND_URL = "http://127.0.0.1:5000"
LOAD_CHAT_URL = f"{BACKEND_URL}/load_chat/"
SAVE_CHAT_URL = f"{BACKEND_URL}/save_chat/"
DELETE_CHAT_URL = f"{BACKEND_URL}/delete_chat/"
UPLOAD_PDF_URL = f"{BACKEND_URL}/upload_pdf/"
CHAT_URL = f"{BACKEND_URL}/chat/"
RAG_CHAT_URL = f"{BACKEND_URL}/rag_chat/"
AUTH_TOKEN_URL = f"{BACKEND_URL}/auth/token"

# Entra External ID URLs
ENTRA_AUTHORITY = f"https://{authority_domain}/{tenant_name}"
REDIRECT_URI = "http://divstar.digital/auth/redirect"

# Initialize MSAL app
app = msal.PublicClientApplication(
    client_id,
    authority=ENTRA_AUTHORITY
)

# Add Arabic support CSS
def add_arabic_support():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap');
    
    [data-language="العربية"] {
        font-family: 'Tajawal', sans-serif;
        direction: rtl;
        text-align: right;
    }
    </style>
    """, unsafe_allow_html=True)

# Initialize session state
if "history_chats" not in st.session_state:
    st.session_state["history_chats"] = []
if "current_chat" not in st.session_state:
    st.session_state["current_chat"] = None
if "chat_names" not in st.session_state:
    st.session_state["chat_names"] = {}
if "token" not in st.session_state:
    st.session_state["token"] = None
if "user_name" not in st.session_state:
    st.session_state["user_name"] = None
if "language" not in st.session_state:
    st.session_state["language"] = "English"

# Functions to manage chats
def load_chats_from_db():
    if not st.session_state["token"]:
        return
        
    headers = {"Authorization": f"Bearer {st.session_state['token']}"}
    response = requests.get(LOAD_CHAT_URL, headers=headers)

    if response.status_code == 200:
        records = response.json()
        for record in records:
            chat_id = record['id']
            messages = record['messages']
            name = record['chat_name']
            pdf_path = record['pdf_path']
            pdf_name = record['pdf_name']
            pdf_uuid = record['pdf_uuid']
            language = record.get('language', 'English')
            st.session_state["history_chats"].append({
                "id": chat_id, 
                "messages": messages, 
                "pdf_name": pdf_name, 
                "pdf_path": pdf_path, 
                "pdf_uuid": pdf_uuid,
                "language": language
            })
            st.session_state["chat_names"][chat_id] = name
    else:
        st.error(f"Failed to retrieve data. Status code: {response.status_code}")

def save_chat_to_db(chat_id, chat_name, messages, pdf_name, pdf_path, pdf_uuid, language="English"):
    if not st.session_state["token"]:
        st.warning("Please sign in to save your chat.")
        return
        
    payload = {
        "chat_id": chat_id,
        "chat_name": chat_name,
        "messages": messages,
        "pdf_name": pdf_name,
        "pdf_path": pdf_path,
        "pdf_uuid": pdf_uuid,
        "language": language
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {st.session_state['token']}"
    }

    response = requests.post(SAVE_CHAT_URL, json=payload, headers=headers)

    if response.status_code != 200:
        st.error(f"Failed to save data. Status code: {response.status_code}")

def create_chat_with_pdf(chat_name, uploaded_pdf):
    if not st.session_state["token"]:
        st.warning("Please sign in to create a chat.")
        return
        
    with st.spinner("Uploading and Processing document, please wait..."):
        files = {"file": (uploaded_pdf.name, uploaded_pdf.getvalue(), "application/pdf")}
        headers = {"Authorization": f"Bearer {st.session_state['token']}"}

        response = requests.post(UPLOAD_PDF_URL, files=files, headers=headers)

        if response.status_code == 200:
            result = response.json()
            pdf_path = result["pdf_path"]
            pdf_uuid = result["pdf_uuid"]
            language = result.get("language", "English")

            new_chat_id = str(uuid.uuid4())
            new_chat = {
                "id": new_chat_id, 
                "messages": [], 
                "pdf_name": uploaded_pdf.name, 
                "pdf_path": pdf_path, 
                "pdf_uuid": pdf_uuid,
                "language": language
            }
            st.session_state["history_chats"].insert(0, new_chat)
            st.session_state["chat_names"][new_chat_id] = chat_name
            st.session_state["current_chat"] = new_chat_id
            save_chat_to_db(new_chat_id, chat_name, [], uploaded_pdf.name, pdf_path, pdf_uuid, language)
            st.success("Success!")
        else:
            st.error("Failed to upload PDF.")

def create_chat(chat_name):
    if not st.session_state["token"]:
        st.warning("Please sign in to create a chat.")
        return
        
    new_chat_id = str(uuid.uuid4())
    new_chat = {
        "id": new_chat_id, 
        "messages": [], 
        "pdf_name": None, 
        "pdf_path": None, 
        "pdf_uuid": None,
        "language": st.session_state["language"]
    }
    st.session_state["history_chats"].insert(0, new_chat)
    st.session_state["chat_names"][new_chat_id] = chat_name
    st.session_state["current_chat"] = new_chat_id
    
    save_chat_to_db(new_chat_id, chat_name, [], None, None, None, st.session_state["language"])

def delete_chat():
    if not st.session_state["token"]:
        st.warning("Please sign in to delete a chat.")
        return
        
    if st.session_state["current_chat"]:
        chat_id = st.session_state["current_chat"]
        st.session_state["history_chats"] = [
            chat for chat in st.session_state["history_chats"] if chat["id"] != chat_id
        ]
        del st.session_state["chat_names"][chat_id]
        
        payload = {"chat_id": chat_id}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {st.session_state['token']}"
        }

        response = requests.post(DELETE_CHAT_URL, json=payload, headers=headers)

        if response.status_code != 200:
            st.error(f"Failed to delete data. Status code: {response.status_code}")

        st.session_state["current_chat"] = (
            st.session_state["history_chats"][0]["id"] if st.session_state["history_chats"] else None
        )

def select_chat(chat_id):
    st.session_state["current_chat"] = chat_id
    # Update language based on selected chat
    for chat in st.session_state["history_chats"]:
        if chat["id"] == chat_id:
            if "language" in chat:
                st.session_state["language"] = chat["language"]
            break

# Authentication functions
def sign_in():
    auth_url = app.get_authorization_request_url(
        ["https://graph.microsoft.com/User.Read"],
        redirect_uri=REDIRECT_URI
    )
    st.markdown(f"[Click here to sign in]({auth_url})")

def sign_up():
    # For sign up with Microsoft Entra External ID
    auth_url = app.get_authorization_request_url(
        ["https://graph.microsoft.com/User.Read"],
        redirect_uri=REDIRECT_URI
    )
    st.markdown(f"[Click here to sign up]({auth_url})")

def reset_password():
    # For password reset with Microsoft Entra External ID
    reset_url = app.get_authorization_request_url(
        ["https://graph.microsoft.com/User.Read"],
        redirect_uri=REDIRECT_URI
    )
    st.markdown(f"[Click here to reset your password]({reset_url})")

def sign_out():
    # Clear session state
    st.session_state["token"] = None
    st.session_state["user_name"] = None
    st.session_state["history_chats"] = []
    st.session_state["current_chat"] = None
    st.session_state["chat_names"] = {}
    st.experimental_rerun()

# Handle authentication code from URL
def handle_auth_code():
    query_params = st.experimental_get_query_params()
    if "code" in query_params:
        code = query_params["code"][0]
        
        # Exchange code for token
        payload = {
            "code": code,
            "redirect_uri": REDIRECT_URI
        }
        headers = {"Content-Type": "application/json"}
        
        response = requests.post(AUTH_TOKEN_URL, json=payload, headers=headers)
        
        if response.status_code == 200:
            token_data = response.json()
            st.session_state["token"] = token_data.get("access_token")
            
            # Extract user name from ID token claims
            if "id_token_claims" in token_data:
                st.session_state["user_name"] = token_data["id_token_claims"].get("name", "User")
            
            # Clear the URL parameters
            st.experimental_set_query_params()
            
            # Load user's chats
            load_chats_from_db()
            
            st.success("Successfully signed in!")
            st.experimental_rerun()
        else:
            st.error("Failed to authenticate. Please try again.")
            st.experimental_set_query_params()

# Apply Arabic support if enabled
if support_arabic:
    add_arabic_support()

# Handle authentication code if present
handle_auth_code()

# Define translations
translations = {
    "English": {
        "welcome": "Welcome to the Chatbot",
        "ask": "Ask a question",
        "send": "Send",
        "clear": "Clear chat",
        "upload": "Upload document",
        "history": "Chat history",
        "language_toggle": "العربية",
        "sign_in": "Sign In",
        "sign_up": "Sign Up",
        "sign_out": "Sign Out",
        "reset_password": "Reset Password",
        "create_chat": "Create New Chat",
        "create_chat_pdf": "Create New Chat with PDF",
        "delete_chat": "Delete Chat",
        "select_chat": "Select Chat",
        "enter_chat_name": "Enter Chat Name:",
        "chat_name_empty": "Chat name cannot be empty.",
        "upload_pdf_first": "Please upload a PDF file before creating the chat.",
        "no_chat_selected": "No chat selected. Use the sidebar to create or select a chat.",
        "current_chat": "Current Chat:",
        "associate_with": "Associate with:",
        "welcome_user": "Welcome, {}!"
    },
    "العربية": {
        "welcome": "مرحبًا بك في روبوت المحادثة",
        "ask": "اطرح سؤالاً",
        "send": "إرسال",
        "clear": "مسح المحادثة",
        "upload": "تحميل مستند",
        "history": "سجل المحادثة",
        "language_toggle": "English",
        "sign_in": "تسجيل الدخول",
        "sign_up": "إنشاء حساب",
        "sign_out": "تسجيل الخروج",
        "reset_password": "إعادة تعيين كلمة المرور",
        "create_chat": "إنشاء محادثة جديدة",
        "create_chat_pdf": "إنشاء محادثة جديدة مع PDF",
        "delete_chat": "حذف المحادثة",
        "select_chat": "اختر محادثة",
        "enter_chat_name": "أدخل اسم المحادثة:",
        "chat_name_empty": "لا يمكن أن يكون اسم المحادثة فارغًا.",
        "upload_pdf_first": "يرجى تحميل ملف PDF قبل إنشاء المحادثة.",
        "no_chat_selected": "لم يتم تحديد محادثة. استخدم الشريط الجانبي لإنشاء أو تحديد محادثة.",
        "current_chat": "المحادثة الحالية:",
        "associate_with": "مرتبط بـ:",
        "welcome_user": "مرحبًا، {}!"
    }
}

# Sidebar
with st.sidebar:
    # Language toggle
    st.markdown("### Language / اللغة")
    if st.button(translations[st.session_state["language"]]["language_toggle"]):
        st.session_state["language"] = "العربية" if st.session_state["language"] == "English" else "English"
        st.experimental_rerun()
    
    # Get translations for current language
    t = translations[st.session_state["language"]]
    
    # Authentication section
    st.title("Authentication")
    
    if not st.session_state["token"]:
        col1, col2 = st.columns(2)
        with col1:
            if st.button(t["sign_in"]):
                sign_in()
        with col2:
            if st.button(t["sign_up"]):
                sign_up()
        
        if st.button(t["reset_password"]):
            reset_password()
    else:
        st.write(t["welcome_user"].format(st.session_state["user_name"]))
        if st.button(t["sign_out"]):
            sign_out()
    
    # Chat management section (only visible when authenticated)
    if st.session_state["token"]:
        st.title(t["history"])
        
        uploaded_pdf = st.file_uploader(t["upload"], type="pdf", key="pdf_uploader")
        
        chat_name = st.text_input(t["enter_chat_name"], key="new_chat_name")
        
        if st.button(t["create_chat"]):
            if chat_name.strip():
                create_chat(chat_name.strip())
            else:
                st.warning(t["chat_name_empty"])
        
        if st.button(t["create_chat_pdf"]):
            if not uploaded_pdf:
                st.warning(t["upload_pdf_first"])
            elif chat_name.strip():
                create_chat_with_pdf(chat_name.strip(), uploaded_pdf)
            else:
                st.warning(t["chat_name_empty"])
        
        if st.session_state["history_chats"]:
            chat_options = {
                chat["id"]: st.session_state["chat_names"][chat["id"]]
                for chat in st.session_state["history_chats"]
            }
            selected_chat = st.radio(
                t["select_chat"],
                options=list(chat_options.keys()),
                format_func=lambda x: chat_options[x],
                key="chat_selector",
                on_change=lambda: select_chat(st.session_state.chat_selector),
            )
            st.session_state["current_chat"] = selected_chat
            
            st.button(t["delete_chat"], on_click=delete_chat)

# Main Content
if st.session_state["language"] == "العربية":
    st.markdown(f'<div data-language="العربية"><h1>{t["welcome"]}</h1></div>', unsafe_allow_html=True)
else:
    st.title(t["welcome"])

# Check if user is authenticated
if not st.session_state["token"]:
    st.warning("Please sign in to use the chatbot.")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button(t["sign_in"] + " →"):
            sign_in()
    with col2:
        if st.button(t["sign_up"] + " →"):
            sign_up()
else:
    if st.session_state["current_chat"]:
        chat_id = st.session_state["current_chat"]
        chat_name = st.session_state["chat_names"][chat_id]
        
        if st.session_state["language"] == "العربية":
            st.markdown(f'<div data-language="العربية"><h3>{t["current_chat"]} {chat_name}</h3></div>', unsafe_allow_html=True)
        else:
            st.subheader(f"{t['current_chat']} {chat_name}")

        current_chat = next(
            (chat for chat in st.session_state["history_chats"] if chat["id"] == chat_id),
            None,
        )

        if current_chat:
            if current_chat["pdf_name"]:
                pdf_name = current_chat["pdf_name"]
                if st.session_state["language"] == "العربية":
                    st.markdown(f'<div data-language="العربية"><h4>{t["associate_with"]} {pdf_name}</h4></div>', unsafe_allow_html=True)
                else:
                    st.subheader(f"{t['associate_with']} {pdf_name}")

            for message in current_chat["messages"]:
                with st.chat_message(message["role"]):
                    if st.session_state["language"] == "العربية" and support_arabic:
                        st.markdown(f'<div data-language="العربية">{message["content"]}</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(message["content"])

            if prompt := st.chat_input(t["ask"]):
                current_chat["messages"].append({"role": "user", "content": prompt})
                
                # Detect language if Arabic support is enabled
                detected_language = st.session_state["language"]
                if support_arabic:
                    try:
                        detected = detect(prompt)
                        if detected == "ar":
                            detected_language = "العربية"
                            if st.session_state["language"] != "العربية":
                                st.session_state["language"] = "العربية"
                                t = translations["العربية"]
                    except:
                        pass
                
                with st.chat_message("user"):
                    if detected_language == "العربية" and support_arabic:
                        st.markdown(f'<div data-language="العربية">{prompt}</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(prompt)

                with st.chat_message("assistant"):
                    payload = {
                        "messages": [
                            {"role": m["role"], "content": m["content"]}
                            for m in current_chat["messages"]
                        ],
                        "language": detected_language
                    }
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {st.session_state['token']}"
                    }

                    if current_chat["pdf_uuid"]:
                        payload["pdf_uuid"] = current_chat["pdf_uuid"]
                        chat_target_url = RAG_CHAT_URL
                    else:
                        chat_target_url = CHAT_URL

                    # Stream approach
                    def get_stream_response():
                        with requests.post(chat_target_url, json=payload, headers=headers, stream=True) as r:
                            for chunk in r:
                                yield chunk.decode("utf-8")

                    response = st.write_stream(get_stream_response)
                    current_chat["messages"].append({"role": "assistant", "content": response})
                    
                    # Update language in chat if detected
                    if detected_language != current_chat.get("language", "English"):
                        current_chat["language"] = detected_language
                    
                    save_chat_to_db(
                        chat_id, 
                        chat_name, 
                        current_chat["messages"], 
                        current_chat["pdf_name"], 
                        current_chat["pdf_path"], 
                        current_chat["pdf_uuid"],
                        current_chat["language"]
                    )
    else:
        if st.session_state["language"] == "العربية":
            st.markdown(f'<div data-language="العربية">{t["no_chat_selected"]}</div>', unsafe_allow_html=True)
        else:
            st.write(t["no_chat_selected"])
