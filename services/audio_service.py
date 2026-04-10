from gtts import gTTS
import os
from typing import Optional
from pydub import AudioSegment

class AudioService:
    def __init__(self):
        pass

    def generate_audio_from_text(self, text: str, output_path: str, lang: str = 'en') -> str:
        """Generate audio file from text using Google Text-to-Speech."""
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Generate audio
            tts = gTTS(text=text, lang=lang, slow=False)
            tts.save(output_path)

            return output_path

        except Exception as e:
            raise Exception(f"Audio generation error: {str(e)}")

    def generate_audio_from_pdf_content(self, content: dict, output_path: str) -> str:
        """Generate audio from simplified PDF content."""
        try:
            # Combine all content into readable text
            audio_text = self._create_audio_script(content)

            # Generate audio
            return self.generate_audio_from_text(audio_text, output_path)

        except Exception as e:
            raise Exception(f"PDF to audio conversion error: {str(e)}")

    def _create_audio_script(self, content: dict) -> str:
        """Create a readable script from PDF content for audio."""
        script_parts = []

        # Title
        if "title" in content:
            script_parts.append(f"{content['title']}.")
            script_parts.append("") # Pause

        # Introduction
        if "introduction" in content:
            script_parts.append(content["introduction"])
            script_parts.append("") # Pause

        # Sections
        if "sections" in content:
            for section in content["sections"]:
                script_parts.append(f"{section['heading']}.")
                script_parts.append(section["content"])

                if "points" in section:
                    for point in section["points"]:
                        script_parts.append(point)

                script_parts.append("") # Pause between sections

        # Summary
        if "summary" in content:
            script_parts.append("Summary.")
            script_parts.append(content["summary"])
            script_parts.append("")

        # Key takeaways
        if "key_takeaways" in content:
            script_parts.append("Key Takeaways.")
            for i, takeaway in enumerate(content["key_takeaways"], 1):
                script_parts.append(f"Number {i}. {takeaway}")

        return " ".join(script_parts)

    def generate_audio_from_transcript(self, transcript: str, output_path: str) -> str:
        """Generate audio from video transcript - extracts dialogue text."""
        try:
            import re
            
            # Extract only the dialogue text from transcript
            # Pattern matches "User A: text" or "A: text" or "User B: text" or "B: text"
            pattern = re.compile(r'^(?:User\s*)?[AB]\s*:\s*(.+)$', re.IGNORECASE | re.MULTILINE)
            matches = pattern.findall(transcript)
            
            if not matches:
                # Fallback: use entire transcript if pattern doesn't match
                audio_text = transcript
            else:
                # Join all dialogue text with pauses
                audio_text = ". ".join(matches)
            
            # Clean up any markdown or special characters
            audio_text = self._clean_text_for_audio(audio_text)
            
            # Generate audio
            return self.generate_audio_from_text(audio_text, output_path)
            
        except Exception as e:
            raise Exception(f"Transcript to audio conversion error: {str(e)}")
    
    def _clean_text_for_audio(self, text: str) -> str:
        """Clean text for better audio generation."""
        import re
        
        # Remove markdown formatting
        text = re.sub(r'\*{1,3}', '', text)  # Remove asterisks
        text = re.sub(r'_{1,2}', '', text)    # Remove underscores
        text = re.sub(r'`+', '', text)        # Remove backticks
        text = re.sub(r'#+\s*', '', text)     # Remove headings
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # Remove links but keep text
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()

    def convert_to_mp3(self, input_path: str, output_path: str) -> str:
        """Convert audio file to MP3 format."""
        try:
            audio = AudioSegment.from_file(input_path)
            audio.export(output_path, format="mp3", bitrate="192k")
            return output_path
        except Exception as e:
            raise Exception(f"Audio conversion error: {str(e)}")