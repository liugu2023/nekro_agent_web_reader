from nekro_agent.api.plugin import NekroPlugin, dynamic_import_pkg, SandboxMethodType, ConfigBase
from nekro_agent.api.schemas import AgentCtx
from pydantic import Field
import re
from urllib.parse import urlparse, urljoin
from typing import Dict, List, Optional

# 创建插件实例
plugin = NekroPlugin(
    name="增强版网页内容读取器",
    module_name="web_reader",
    description="支持多种网站类型的智能网页内容提取工具",
    version="2.0.0",
    author="liugu",
    url="none"
)

# 动态导入外部依赖
requests = dynamic_import_pkg("requests>=2.25.0,<3.0.0")
bs4 = dynamic_import_pkg("beautifulsoup4>=4.9.0,<5.0.0", import_name="bs4")

@plugin.mount_config()
class WebReaderConfig(ConfigBase):
    """网页内容读取器配置"""
    
    DEFAULT_TIMEOUT: int = Field(
        default=30,
        title="默认请求超时时间",
        description="HTTP请求的默认超时时间（秒）",
        ge=5,
        le=300,
    )
    
    MAX_CONTENT_LENGTH: int = Field(
        default=15000,
        title="最大内容长度",
        description="返回内容的最大字符数",
        ge=1000,
        le=100000,
    )
    
    USER_AGENT: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        title="用户代理字符串",
        description="HTTP请求时使用的User-Agent头",
    )
    
    EXTRACT_LINKS: bool = Field(
        default=True,
        title="提取链接",
        description="是否提取页面中的主要链接",
    )
    
    EXTRACT_IMAGES: bool = Field(
        default=True,
        title="提取图片",
        description="是否提取页面中的图片URL",
    )

config: WebReaderConfig = plugin.get_config(WebReaderConfig)


class WebContentExtractor:
    """网页内容提取器"""
    
    @staticmethod
    def clean_text(text: str) -> str:
        """清理文本内容"""
        # 替换多个空白为单个空格
        text = re.sub(r'\s+', ' ', text)
        # 移除首尾空白
        text = text.strip()
        return text
    
    @staticmethod
    def extract_metadata(soup) -> Dict[str, str]:
        """提取网页元数据"""
        metadata = {}
        
        # 提取标题
        title_tag = soup.find('title')
        metadata['title'] = title_tag.get_text().strip() if title_tag else "无标题"
        
        # 提取描述
        desc_tag = soup.find('meta', attrs={'name': 'description'}) or \
                   soup.find('meta', attrs={'property': 'og:description'})
        metadata['description'] = desc_tag.get('content', '').strip() if desc_tag else ""
        
        # 提取关键词
        keywords_tag = soup.find('meta', attrs={'name': 'keywords'})
        metadata['keywords'] = keywords_tag.get('content', '').strip() if keywords_tag else ""
        
        # 提取作者
        author_tag = soup.find('meta', attrs={'name': 'author'}) or \
                     soup.find('meta', attrs={'property': 'article:author'})
        metadata['author'] = author_tag.get('content', '').strip() if author_tag else ""
        
        return metadata
    
    @staticmethod
    def extract_main_content(soup) -> str:
        """智能提取主要内容"""
        # 移除无用标签
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
            tag.decompose()
        
        # 优先查找文章主体
        main_content = None
        
        # 常见的文章容器
        content_selectors = [
            'article',
            '[role="main"]',
            'main',
            '.article-content',
            '.post-content',
            '.entry-content',
            '#content',
            '.content',
        ]
        
        for selector in content_selectors:
            main_content = soup.select_one(selector)
            if main_content:
                break
        
        # 如果没找到，使用 body
        if not main_content:
            main_content = soup.find('body')
        
        if not main_content:
            return ""
        
        # 提取文本
        text = main_content.get_text(separator='\n', strip=True)
        return WebContentExtractor.clean_text(text)
    
    @staticmethod
    def extract_links(soup, base_url: str, limit: int = 10) -> List[Dict[str, str]]:
        """提取重要链接"""
        links = []
        seen_urls = set()
        
        for a_tag in soup.find_all('a', href=True):
            if len(links) >= limit:
                break
            
            href = a_tag.get('href', '').strip()
            text = a_tag.get_text().strip()
            
            # 跳过空链接、锚点、JavaScript
            if not href or href.startswith('#') or href.startswith('javascript:'):
                continue
            
            # 转换为绝对URL
            absolute_url = urljoin(base_url, href)
            
            # 去重
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            
            # 只保留 http/https 链接
            if absolute_url.startswith(('http://', 'https://')):
                links.append({
                    'text': text[:50] if text else '无文本',
                    'url': absolute_url
                })
        
        return links
    
    @staticmethod
    def extract_images(soup, base_url: str, limit: int = 10) -> List[str]:
        """提取图片URL"""
        images = []
        seen_urls = set()
        
        for img_tag in soup.find_all('img'):
            if len(images) >= limit:
                break
            
            # 尝试多个属性
            src = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-lazy-src')
            
            if not src:
                continue
            
            # 转换为绝对URL
            absolute_url = urljoin(base_url, src.strip())
            
            # 去重和过滤
            if absolute_url in seen_urls or not absolute_url.startswith(('http://', 'https://')):
                continue
            
            # 过滤掉小图标和像素图
            if any(x in absolute_url.lower() for x in ['icon', 'logo', '1x1', 'pixel']):
                continue
            
            seen_urls.add(absolute_url)
            images.append(absolute_url)
        
        return images


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "fetch_webpage", "读取并解析网页内容")
async def fetch_webpage(_ctx: AgentCtx, url: str, timeout: int = None) -> str:
    """读取并智能解析网页内容
    
    Args:
        url: 要读取的网址
        timeout: 请求超时时间（秒）
        
    Returns:
        格式化的网页内容
    """
    try:
        # 检查依赖
        if not requests:
            return "❌ 错误：requests包未安装"
        
        if not bs4:
            return "⚠️ 警告：BeautifulSoup未安装，将使用简化模式"
        
        # 参数验证
        if not url or not isinstance(url, str):
            return "❌ 错误：URL不能为空"
        
        # URL格式验证
        try:
            parsed = urlparse(url)
            if not all([parsed.scheme, parsed.netloc]):
                return f"❌ 错误：无效的URL '{url}'，需要包含 http:// 或 https://"
        except Exception as e:
            return f"❌ 错误：URL解析失败 - {str(e)}"
        
        # 配置
        timeout = timeout or config.DEFAULT_TIMEOUT
        max_length = config.MAX_CONTENT_LENGTH
        headers = {'User-Agent': config.USER_AGENT}
        
        # 发送请求
        response = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        response.raise_for_status()
        
        # 处理编码
        if response.encoding:
            response.encoding = response.encoding
        else:
            response.encoding = response.apparent_encoding or 'utf-8'
        
        content = response.text
        
        # 使用 BeautifulSoup 解析（如果可用）
        if bs4:
            BeautifulSoup = bs4.BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            extractor = WebContentExtractor()
            
            # 提取元数据
            metadata = extractor.extract_metadata(soup)
            
            # 提取主要内容
            main_text = extractor.extract_main_content(soup)
            
            # 提取链接
            links = []
            if config.EXTRACT_LINKS:
                links = extractor.extract_links(soup, url, limit=10)
            
            # 提取图片
            images = []
            if config.EXTRACT_IMAGES:
                images = extractor.extract_images(soup, url, limit=5)
            
            # 格式化输出
            output_parts = [
                "=" * 60,
                "📄 网页信息",
                "=" * 60,
                f"🔗 URL: {url}",
                f"📌 标题: {metadata['title']}",
                f"✅ 状态码: {response.status_code}",
                f"🌐 编码: {response.encoding}",
            ]
            
            if metadata.get('description'):
                output_parts.append(f"📝 描述: {metadata['description']}")
            
            if metadata.get('author'):
                output_parts.append(f"✍️ 作者: {metadata['author']}")
            
            if metadata.get('keywords'):
                output_parts.append(f"🏷️ 关键词: {metadata['keywords']}")
            
            # 主要内容
            output_parts.extend([
                "",
                "=" * 60,
                "📖 主要内容",
                "=" * 60,
            ])
            
            if main_text:
                preview = main_text[:max_length]
                if len(main_text) > max_length:
                    preview += "\n\n... (内容已截断)"
                output_parts.append(preview)
            else:
                output_parts.append("（未找到主要内容）")
            
            output_parts.append(f"\n📊 总字数: {len(main_text)}")
            
            # 链接
            if links:
                output_parts.extend([
                    "",
                    "=" * 60,
                    f"🔗 重要链接 (共{len(links)}个)",
                    "=" * 60,
                ])
                for i, link in enumerate(links, 1):
                    output_parts.append(f"{i}. {link['text']}")
                    output_parts.append(f"   {link['url']}")
            
            # 图片
            if images:
                output_parts.extend([
                    "",
                    "=" * 60,
                    f"🖼️ 图片资源 (共{len(images)}个)",
                    "=" * 60,
                ])
                for i, img_url in enumerate(images, 1):
                    output_parts.append(f"{i}. {img_url}")
            
            return "\n".join(output_parts)
        
        else:
            # 简化模式（无 BeautifulSoup）
            # 使用正则表达式提取
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "无标题"
            
            # 移除脚本和样式
            clean = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
            
            # 移除HTML标签
            text = re.sub(r'<[^>]+>', '', clean)
            text = re.sub(r'\s+', ' ', text).strip()
            
            preview = text[:max_length]
            if len(text) > max_length:
                preview += "\n\n... (内容已截断)"
            
            return f"""{"=" * 60}
📄 网页信息 (简化模式)
{"=" * 60}
🔗 URL: {url}
📌 标题: {title}
✅ 状态码: {response.status_code}
🌐 编码: {response.encoding}

{"=" * 60}
📖 内容
{"=" * 60}
{preview}

📊 总字数: {len(text)}

⚠️ 提示: 安装 beautifulsoup4 以获得更好的解析效果"""
        
    except requests.exceptions.Timeout:
        return f"❌ 错误：请求超时（{timeout}秒），目标网站响应过慢"
    except requests.exceptions.ConnectionError:
        return "❌ 错误：连接失败，请检查网络或目标网站是否可访问"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if hasattr(e, 'response') else '未知'
        return f"❌ 错误：HTTP {status} - 服务器返回错误"
    except requests.exceptions.RequestException as e:
        return f"❌ 错误：请求异常 - {str(e)}"
    except Exception as e:
        return f"❌ 错误：{type(e).__name__}: {str(e)}"