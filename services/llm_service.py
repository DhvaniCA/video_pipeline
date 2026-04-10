from openai import OpenAI
import google.generativeai as genai
from typing import Dict
from config import config
import json

class LLMService:
    def __init__(self):
        self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        genai.configure(api_key=config.GOOGLE_API_KEY)
        self.gemini_model = genai.GenerativeModel('gemini-pro')

    def simplify_content_with_openai(self, text: str) -> Dict:
        """Use OpenAI to simplify content for CA students."""
        try:
            prompt = f"""
You are an expert CA (Chartered Accountant) educator. Simplify the following content for CA students.

Create a structured, easy-to-understand document with:
1. A clear title
2. An introduction (2-3 sentences)
3. Main sections with headings and clear explanations
4. Key points for each section
5. A summary
6. Key takeaways (5-7 points)

Make it conversational and easy to understand while maintaining accuracy.

Original Content:
{text[:4000]}

Return the response in JSON format with this structure:
{{
    "title": "Document Title",
    "introduction": "Introduction text",
    "sections": [
        {{
            "heading": "Section Heading",
            "content": "Section content",
            "points": ["Point 1", "Point 2"]
        }}
    ],
    "summary": "Summary text",
    "key_takeaways": ["Takeaway 1", "Takeaway 2"]
}}
"""
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a CA education expert who simplifies complex accounting concepts."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )

            content = json.loads(response.choices[0].message.content)
            return content

        except Exception as e:
            raise Exception(f"OpenAI simplification error: {str(e)}")

    def simplify_content_with_gemini(self, text: str) -> Dict:
        """Use Gemini to simplify content for CA students."""
        try:
            prompt = f"""
You are an expert CA (Chartered Accountant) educator. Simplify the following content for CA students.

Create a structured, easy-to-understand document with:
1. A clear title
2. An introduction (2-3 sentences)
3. Main sections with headings and clear explanations
4. Key points for each section
5. A summary
6. Key takeaways (5-7 points)

Make it conversational and easy to understand while maintaining accuracy.

Original Content:
{text[:4000]}

Return the response in JSON format with this structure:
{{
    "title": "Document Title",
    "introduction": "Introduction text",
    "sections": [
        {{
            "heading": "Section Heading",
            "content": "Section content",
            "points": ["Point 1", "Point 2"]
        }}
    ],
    "summary": "Summary text",
    "key_takeaways": ["Takeaway 1", "Takeaway 2"]
}}
"""
            response = self.gemini_model.generate_content(prompt)

            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:-3].strip()
            elif response_text.startswith("```"):
                response_text = response_text[3:-3].strip()

            content = json.loads(response_text)
            return content

        except Exception as e:
            raise Exception(f"Gemini simplification error: {str(e)}")

    def generate_video_transcript(self, simplified_content: Dict, use_openai: bool = True) -> str:
        """Generate a conversational transcript for animated video."""
        try:
            content_summary = f"""
Title: {simplified_content.get('title', 'CA Topic')}
Introduction: {simplified_content.get('introduction', '')}
Sections: {len(simplified_content.get('sections', []))}
Key Takeaways: {', '.join(simplified_content.get('key_takeaways', [])[:3])}
"""
            prompt = f"""
Create a 5-7 minute conversational script between two characters (User A and User B) discussing this CA topic.

Content Summary:
{content_summary}

Requirements:
1. Make it a natural, engaging dialogue
2. User A asks questions, User B explains (like a teacher-student conversation)
3. Include the main concepts from the simplified content
4. Keep it between 5-7 minutes when spoken (approximately 750-1050 words)
5. Make it easy to understand for CA students
6. End with key takeaways

Format each line as:
User A: [dialogue]
User B: [dialogue]

Create an educational but conversational script.
"""
            if use_openai:
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a script writer for educational content."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.8
                )
                return response.choices[0].message.content
            else:
                response = self.gemini_model.generate_content(prompt)
                return response.text

        except Exception as e:
            raise Exception(f"Transcript generation error: {str(e)}")