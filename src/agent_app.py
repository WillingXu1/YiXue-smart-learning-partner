"""
智能学习伙伴 - ReAct Agent 版本（带记忆，适配 LangGraph 1.0.10 + LangChain 1.2.10）
已修复：
- create_react_agent 导入路径
- messages_modifier 参数移除
- 系统提示通过 SystemMessage 注入
- Gradio 文件上传路径处理
- Chatbot role/content 格式
"""

import os
import tempfile

import gradio as gr
from dotenv import load_dotenv

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.chat_models import ChatZhipuAI
from langchain_community.embeddings import ZhipuAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

load_dotenv()

# ==================== 全局变量 ====================
llm = None
embeddings = None
vectorstore = None
graph = None
memory_saver = None

def init_ai():
    global llm, embeddings, memory_saver, graph
    api_key = os.getenv("ZHIPUAI_API_KEY")
    if not api_key:
        raise ValueError("请设置 ZHIPUAI_API_KEY")
    
    llm = ChatZhipuAI(
        model="glm-4.7",
        temperature=0.3,
        max_tokens=3000,
    )
    embeddings = ZhipuAIEmbeddings(model="embedding-3")
    
    memory_saver = MemorySaver()
    
    print("✅ ZhipuAI + LangGraph Memory 初始化成功")

# ==================== 工具定义 ====================

@tool
def search_textbook(query: str) -> str:
    """检索用户上传的 PDF 教材内容。

    这个工具用于从已上传的教材 PDF 中检索与查询最相关的文本片段。
    输入：查询关键词或问题描述（字符串）
    输出：最相关的教材内容片段（字符串），如果未上传 PDF 会返回错误提示。
    """
    if vectorstore is None:
        return "错误：还没有上传 PDF 教材，请先上传文档。"
    docs = vectorstore.as_retriever(search_type="mmr", search_kwargs={"k": 8}).invoke(query)
    return "\n\n".join([doc.page_content for doc in docs]) + "\n（检索到的教材关键片段）"

@tool
def generate_quiz_tool(topic: str) -> str:
    """根据指定主题或知识点生成测验题目。

    这个工具会自动生成 5 道测验题（3 道单选 + 2 道简答），适合考前复习。
    输入：主题或知识点描述（字符串，例如“线性代数矩阵运算”）
    输出：Markdown 格式的题目 + 答案 + 解析（字符串）
    """
    prompt = PromptTemplate.from_template(
        """基于以下主题生成 5 道高质量测验题：
主题/内容：{topic}

要求：
- 1-3：单选题（4 选项 + 正确答案 + 解析）
- 4-5：简答题（答案 + 评分要点）
Markdown 格式"""
    )
    return (prompt | llm).invoke({"topic": topic}).content

@tool
def generate_mindmap_tool(topic: str) -> str:
    """根据主题生成 Markdown 格式的思维导图大纲。

    这个工具用于帮助用户快速构建知识结构，便于记忆和复习。
    输入：主题或知识点（字符串，例如“傅里叶变换”）
    输出：Markdown 层级列表的思维导图（字符串）
    """
    prompt = PromptTemplate.from_template(
        """基于以下主题生成 Markdown 思维导图：
主题/内容：{topic}

要求：
- 3-5 个一级节点
- 每个一级下 2-4 个二级
- 用 emoji + 关键词"""
    )
    return (prompt | llm).invoke({"topic": topic}).content

# ==================== PDF 处理 ====================
def upload_and_build_vector(file_path: str):
    global vectorstore, graph
    try:
        if not file_path:
            return "请小主上传 PDF 文件"
        
        loader = PyPDFLoader(file_path)
        docs = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
        splits = text_splitter.split_documents(docs)
        
        vectorstore = FAISS.from_documents(splits, embeddings)
        
        # LangGraph 1.0.10 兼容写法：不使用 messages_modifier
        # 系统提示通过在输入消息中添加 SystemMessage 实现
        graph = create_react_agent(
            model=llm,
            tools=[search_textbook, generate_quiz_tool, generate_mindmap_tool],
            checkpointer=memory_saver
        )
        
        return f"✅ PDF 处理完成！共 {len(splits)} 个块，尊贵的小主，您可以开始提问啦，小易随时为你解答。小易预祝您期末科科满分~~~///(^v^)\\\~~~"
    except Exception as e:
        return f"❌ 处理失败：{str(e)}"

# ==================== 聊天函数 ====================
def chat_with_agent(message, history, status):
    global graph
    
    if vectorstore is None:
        return history + [{"role": "user", "content": message}, {"role": "assistant", "content": "请先上传 PDF 教材！"}], status
    
    if graph is None:
        return history + [{"role": "user", "content": message}, {"role": "assistant", "content": "知识库未加载，请重新上传 PDF。"}], status
    
    try:
        config = {"configurable": {"thread_id": "1"}}
        
        # 在每轮消息开头添加系统提示（兼容 LangGraph 1.0.10）
        system_msg = SystemMessage(content="你是智能学习伙伴，专注于帮助用户消化大学教材中的知识点。请使用工具分析教材内容。")
        user_msg = HumanMessage(content=message)
        
        inputs = {"messages": [system_msg, user_msg]}
        
        full_response = ""
        for chunk in graph.stream(inputs, config):
            if "agent" in chunk:
                messages = chunk["agent"].get("messages", [])
                if messages:
                    last_msg = messages[-1]
                    if hasattr(last_msg, "content"):
                        content = last_msg.content
                        if isinstance(content, list):
                            content = "\n".join([str(item) for item in content])
                        elif isinstance(content, dict):
                            content = str(content)
                        full_response += str(content) + "\n"
        
        answer = full_response.strip() or "无有效输出，请重试。"
    except Exception as e:
        answer = f"Agent 执行出错：{str(e)}"
    
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": answer})
    
    return history, status

def clear_chat():
    return [], "聊天和记忆已清空"

# ==================== Gradio UI ====================
with gr.Blocks(title="易学 - 新一代智能学习伙伴") as demo:
    gr.Markdown("""
    # 📚 易学 - 新一代智能学习伙伴
    上传 PDF → Agent 自主思考、检索、出题、画思维导图，并记住上下文
    """)
    
    status_output = gr.Textbox(label="状态", value="等待上传 PDF...", interactive=False)
    
    with gr.Row():
        pdf_upload = gr.File(label="上传 PDF 教材", file_types=[".pdf"])
        upload_btn = gr.Button("上传并构建知识库")
    
    upload_btn.click(upload_and_build_vector, inputs=pdf_upload, outputs=status_output)
    
    chatbot = gr.Chatbot(height=500)
    msg = gr.Textbox(placeholder="我是小易，可以问我任何关于大学教材的问题，我会记住上下文...", label="你的问题")
    
    with gr.Row():
        send_btn = gr.Button("发送给小易~")
        clear_btn = gr.Button("清空对话 & 记忆")
    
    send_btn.click(chat_with_agent, [msg, chatbot, status_output], [chatbot, status_output])
    msg.submit(chat_with_agent, [msg, chatbot, status_output], [chatbot, status_output])
    clear_btn.click(clear_chat, None, [chatbot, status_output])

def main():
    print("🚀 启动 ReAct Agent 学习伙伴...")
    init_ai()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        debug=True,
        theme=gr.themes.Soft()
    )

if __name__ == "__main__":
    main()