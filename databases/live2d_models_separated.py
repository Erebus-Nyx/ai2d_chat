"""
Live2D Model Management for AI Companion.
Handles dynamic detection and storage of Live2D models and their available motions.
Now uses separated database architecture.
"""

import sqlite3
import json
import logging
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
from .database_manager import get_live2d_connection

class Live2DModelManager:
    """
    Manages Live2D models and their motion data in the separated Live2D database.
    """
    
    def __init__(self, db_path: str = None):
        # Legacy support - db_path is ignored, we use separated databases now
        self.logger = logging.getLogger(__name__)
        # Note: We'll use context managers for connections instead of persistent connection
        self.create_tables()
    
    def create_tables(self):
        """Create Live2D model related tables."""
        with get_live2d_connection() as conn:
            cursor = conn.cursor()
            tables = {
                'live2d_models': """
                    CREATE TABLE IF NOT EXISTS live2d_models (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        model_name TEXT NOT NULL UNIQUE,
                        model_path TEXT NOT NULL,
                        config_file TEXT NOT NULL,
                        model_type TEXT DEFAULT 'Live2D_v4',
                        description TEXT,
                        preview_image TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """,
                
                'live2d_motions': """
                    CREATE TABLE IF NOT EXISTS live2d_motions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        model_id INTEGER NOT NULL,
                        motion_group TEXT NOT NULL,
                        motion_index INTEGER NOT NULL,
                        motion_name TEXT,
                        motion_type TEXT DEFAULT 'body', -- 'body', 'head', 'expression', 'special'
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (model_id) REFERENCES live2d_models (id) ON DELETE CASCADE
                    )
                """
            }
            
            for table_name, table_sql in tables.items():
                cursor.execute(table_sql)
                self.logger.debug(f"Created/verified table: {table_name}")
            
            # Migration: Add preview_image column if it doesn't exist
            try:
                cursor.execute("SELECT preview_image FROM live2d_models LIMIT 1")
            except sqlite3.OperationalError:
                # Column doesn't exist, add it
                self.logger.info("Adding preview_image column to live2d_models table")
                cursor.execute("ALTER TABLE live2d_models ADD COLUMN preview_image TEXT")
                conn.commit()
            
            # Create indexes for better performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_motions_model_id ON live2d_motions(model_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_motions_type ON live2d_motions(motion_type)")
            
            conn.commit()
            self.logger.info("Live2D database tables created/verified")
    
    def scan_models_directory(self, models_base_path: str = None):
        """
        Scan the models directory and register all available Live2D models.
        This also removes models that no longer exist in the filesystem.
        """
        if models_base_path is None:
            try:
                from config.config_manager import ConfigManager
                config_manager = ConfigManager()
                models_base_path = str(config_manager.get_live2d_models_path())
            except ImportError:
                # Fallback if config manager not available
                models_base_path = os.path.expanduser("~/.local/share/ai2d_chat/live2d_models")
        models_path = Path(models_base_path)
        if not models_path.exists():
            self.logger.warning(f"Models directory not found: {models_path}")
            return
        
        self.logger.info(f"Scanning for Live2D models in: {models_path}")
        
        # Get current models in database
        current_models = {model['model_name'] for model in self.get_all_models()}
        found_models = set()
        
        for model_dir in models_path.iterdir():
            if model_dir.is_dir():
                # Look for .model3.json files recursively
                model_files = list(model_dir.glob("**/*.model3.json"))
                if model_files:
                    model_file = model_files[0]  # Use first found
                    # For models in user directory, use relative path from live2d_models
                    rel_model_path = f"live2d_models/{model_dir.name}"
                    # Get relative path from model directory to config file
                    config_relative_path = str(model_file.relative_to(model_dir))
                    self.register_model(
                        model_name=model_dir.name,
                        model_path=rel_model_path,
                        config_file=config_relative_path
                    )
                    # Automatically scan and register motions for this model
                    self.scan_and_register_motions(model_dir.name, str(model_dir))
                    found_models.add(model_dir.name)
        
        # Remove models that no longer exist in filesystem
        models_to_remove = current_models - found_models
        for model_name in models_to_remove:
            self.logger.info(f"Removing model no longer in filesystem: {model_name}")
            self.delete_model(model_name)
    
    def scan_and_register_motions(self, model_name: str, model_path: str):
        """
        Scan a model directory for motion files and register them in the database.
        First tries to parse motions from .model3.json, then falls back to file scanning.
        """
        try:
            model_dir = Path(model_path)
            motions_data = []
            
            # First try to extract motions from .model3.json file
            model3_files = list(model_dir.glob("**/*.model3.json"))
            if model3_files:
                motions_from_json = self._parse_motions_from_model3_json(model3_files[0])
                if motions_from_json:
                    motions_data.extend(motions_from_json)
                    self.logger.info(f"Extracted {len(motions_from_json)} motions from model3.json for: {model_name}")
                
                # Also extract expressions from .model3.json
                expressions_from_json = self._parse_expressions_from_model3_json(model3_files[0])
                if expressions_from_json:
                    motions_data.extend(expressions_from_json)
                    self.logger.info(f"Extracted {len(expressions_from_json)} expressions from model3.json for: {model_name}")
            
            # Fallback: Find all .motion3.json files recursively (if no motions found in model3.json)
            if not motions_data:
                motion_files = list(model_dir.glob("**/*.motion3.json"))
                
                if motion_files:
                    for motion_file in motion_files:
                        # Extract motion name from filename
                        motion_name = motion_file.stem
                        
                        # Try to determine motion group from directory structure
                        motion_group = "default"
                        # Use the immediate parent directory as the group
                        if motion_file.parent.name != model_dir.name:
                            motion_group = motion_file.parent.name
                        
                        # Determine motion type based on filename patterns
                        motion_type = "body"  # default
                        if any(keyword in motion_name.lower() for keyword in ["face", "eye", "blink"]):
                            motion_type = "face"
                        elif any(keyword in motion_name.lower() for keyword in ["head", "nod", "shake"]):
                            motion_type = "head"
                        elif any(keyword in motion_name.lower() for keyword in ["expression", "emotion"]):
                            motion_type = "expression"
                        elif any(keyword in motion_name.lower() for keyword in ["idle"]):
                            motion_type = "idle"
                        
                        motions_data.append({
                            "group": motion_group,
                            "index": len(motions_data),  # Simple indexing
                            "name": motion_name,
                            "type": motion_type
                        })
                    
                    self.logger.info(f"Found {len(motion_files)} individual motion files for: {model_name}")
            
            # Register all found motions
            if motions_data:
                self.register_motions_for_model(model_name, motions_data)
                self.logger.info(f"Registered {len(motions_data)} total motions/expressions for model: {model_name}")
            else:
                self.logger.info(f"No motion files found for model: {model_name}")
            
        except Exception as e:
            self.logger.error(f"Error scanning motions for model {model_name}: {e}")
    
    def _parse_motions_from_model3_json(self, model3_file: Path) -> List[Dict]:
        """Parse motion information from .model3.json file"""
        try:
            import json
            with open(model3_file, 'r', encoding='utf-8') as f:
                model_data = json.load(f)
            
            motions_data = []
            file_refs = model_data.get('FileReferences', {})
            motions = file_refs.get('Motions', {})
            
            for group_name, motion_list in motions.items():
                for index, motion_info in enumerate(motion_list):
                    motion_file = motion_info.get('File', '')
                    motion_name = Path(motion_file).stem if motion_file else f"{group_name}_{index}"
                    
                    # Determine motion type based on group name and filename
                    motion_type = self._determine_motion_type(group_name, motion_name)
                    
                    motions_data.append({
                        "group": group_name,
                        "index": index,
                        "name": motion_name,
                        "type": motion_type
                    })
            
            return motions_data
            
        except Exception as e:
            self.logger.error(f"Error parsing motions from model3.json {model3_file}: {e}")
            return []
    
    def _parse_expressions_from_model3_json(self, model3_file: Path) -> List[Dict]:
        """Parse expression information from .model3.json file"""
        try:
            import json
            with open(model3_file, 'r', encoding='utf-8') as f:
                model_data = json.load(f)
            
            expressions_data = []
            file_refs = model_data.get('FileReferences', {})
            expressions = file_refs.get('Expressions', [])
            
            for index, expression_info in enumerate(expressions):
                expression_name = expression_info.get('Name', f'expression_{index}')
                expression_file = expression_info.get('File', '')
                
                # Clean up the name (remove .exp3.json extension if present)
                if expression_name.endswith('.exp3.json'):
                    expression_name = expression_name[:-10]
                
                expressions_data.append({
                    "group": "expressions",
                    "index": index,
                    "name": expression_name,
                    "type": "expression"
                })
            
            return expressions_data
            
        except Exception as e:
            self.logger.error(f"Error parsing expressions from model3.json {model3_file}: {e}")
            return []
    
    def _determine_motion_type(self, group_name: str, motion_name: str) -> str:
        """Determine motion type based on group name and motion filename"""
        group_lower = group_name.lower()
        motion_lower = motion_name.lower()
        
        # Check group name first
        if group_lower in ['idle', 'breathing']:
            return 'idle'
        elif group_lower in ['tap', 'touch', 'click']:
            return 'interaction'
        elif group_lower in ['flick', 'swipe', 'drag']:
            return 'gesture'
        elif group_lower in ['shake', 'pinch']:
            return 'gesture'
        elif 'face' in group_lower or 'eye' in group_lower:
            return 'face'
        elif 'head' in group_lower:
            return 'head'
        
        # Check motion filename patterns
        if any(keyword in motion_lower for keyword in ["face", "eye", "blink"]):
            return "face"
        elif any(keyword in motion_lower for keyword in ["head", "nod", "shake"]):
            return "head"
        elif any(keyword in motion_lower for keyword in ["idle", "breath"]):
            return "idle"
        elif any(keyword in motion_lower for keyword in ["tap", "touch", "click"]):
            return "interaction"
        
        return "body"  # default
    
    def register_model(self, model_name: str, model_path: str, config_file: str, description: str = None):
        """Register a new Live2D model in the database, avoiding duplicates."""
        try:
            print(f"[DEBUG] Attempting to register model: {model_name}, path: {model_path}, config: {config_file}")
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                # Check if model already exists with same name, path, and config
                cursor.execute("""
                    SELECT id FROM live2d_models WHERE model_name = ? AND model_path = ? AND config_file = ?
                """, (model_name, model_path, config_file))
                existing = cursor.fetchone()
                if existing:
                    model_id = existing['id']
                    print(f"[DEBUG] Model already exists: {model_name} (ID: {model_id})")
                    return model_id
                # If model_name exists but path/config changed, update it
                cursor.execute("SELECT id FROM live2d_models WHERE model_name = ?", (model_name,))
                name_only = cursor.fetchone()
                if name_only:
                    model_id = name_only['id']
                    cursor.execute("""
                        UPDATE live2d_models 
                        SET model_path = ?, config_file = ?, description = ?, last_updated = CURRENT_TIMESTAMP
                        WHERE model_name = ?
                    """, (model_path, config_file, description, model_name))
                    conn.commit()
                    print(f"[DEBUG] Updated existing model: {model_name} (ID: {model_id})")
                    return model_id
                # Otherwise, insert new model
                cursor.execute("""
                    INSERT INTO live2d_models 
                    (model_name, model_path, config_file, description, last_updated)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (model_name, model_path, config_file, description))
                model_id = cursor.lastrowid
                conn.commit()
                print(f"[DEBUG] Registered new model: {model_name} (ID: {model_id})")
                return model_id
        except Exception as e:
            import traceback
            print(f"[DEBUG] Error registering model {model_name}: {e}")
            traceback.print_exc()
            self.logger.error(f"Error registering model {model_name}: {e}")
            return None

    def register_motions_for_model(self, model_name: str, motions_data: List[Dict]):
        """
        Register motion data for a specific model.
        motions_data should be a list of dicts with keys: group, index, name, type
        """
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                # Get model ID
                cursor.execute("SELECT id FROM live2d_models WHERE model_name = ?", (model_name,))
                row = cursor.fetchone()
                if not row:
                    self.logger.error(f"Model not found: {model_name}")
                    return False
                model_id = row['id']
                # Clear existing motions for this model
                cursor.execute("DELETE FROM live2d_motions WHERE model_id = ?", (model_id,))
                # Insert new motions
                for motion in motions_data:
                    cursor.execute("""
                        INSERT INTO live2d_motions 
                        (model_id, motion_group, motion_index, motion_name, motion_type)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        model_id,
                        motion['group'],
                        motion['index'],
                        motion.get('name', f"{motion['group']}_{motion['index']}"),
                        motion.get('type', 'body')
                    ))
                conn.commit()
                self.logger.info(f"Registered {len(motions_data)} motions for model: {model_name}")
                return True
        except Exception as e:
            self.logger.error(f"Error registering motions for {model_name}: {e}")
            return False

    def get_all_models(self) -> List[Dict]:
        """Get all registered Live2D models."""
        with get_live2d_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, model_name, model_path, config_file, description 
                FROM live2d_models 
                ORDER BY model_name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_model_motions(self, model_name: str) -> List[Dict]:
        """Get all motions for a specific model."""
        with get_live2d_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lm.motion_group, lm.motion_index, lm.motion_name, lm.motion_type
                FROM live2d_motions lm
                JOIN live2d_models m ON lm.model_id = m.id
                WHERE m.model_name = ?
                ORDER BY lm.motion_group, lm.motion_index
            """, (model_name,))
            return [dict(row) for row in cursor.fetchall()]

    def get_model_motions_by_type(self, model_name: str, motion_type: str = None) -> List[Dict]:
        """Get motions for a model, optionally filtered by type."""
        with get_live2d_connection() as conn:
            cursor = conn.cursor()
            if motion_type:
                cursor.execute("""
                    SELECT lm.motion_group, lm.motion_index, lm.motion_name, lm.motion_type
                    FROM live2d_motions lm
                    JOIN live2d_models m ON lm.model_id = m.id
                    WHERE m.model_name = ? AND lm.motion_type = ?
                    ORDER BY lm.motion_group, lm.motion_index
                """, (model_name, motion_type))
                return [dict(row) for row in cursor.fetchall()]
            else:
                return self.get_model_motions(model_name)

    def clear_all_models(self):
        """Clear all Live2D models and motions from database (DESTRUCTIVE)."""
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                # First, delete all motions (due to foreign key constraint)
                cursor.execute("DELETE FROM live2d_motions")
                motions_deleted = cursor.rowcount
                # Then, delete all models
                cursor.execute("DELETE FROM live2d_models")
                models_deleted = cursor.rowcount
                conn.commit()
                self.logger.info(f"Cleared database: {models_deleted} models and {motions_deleted} motions deleted")
                return {
                    'models_deleted': models_deleted,
                    'motions_deleted': motions_deleted
                }
        except Exception as e:
            self.logger.error(f"Error clearing all models: {e}")
            raise e

    def delete_model(self, model_name: str):
        """Delete a specific model and all its motions."""
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                # Get model ID first
                cursor.execute("SELECT id FROM live2d_models WHERE model_name = ?", (model_name,))
                result = cursor.fetchone()
                if not result:
                    raise ValueError(f"Model '{model_name}' not found")
                model_id = result['id']
                # Delete motions first (due to foreign key constraint)
                cursor.execute("DELETE FROM live2d_motions WHERE model_id = ?", (model_id,))
                motions_deleted = cursor.rowcount
                # Delete the model
                cursor.execute("DELETE FROM live2d_models WHERE id = ?", (model_id,))
                models_deleted = cursor.rowcount
                conn.commit()
                self.logger.info(f"Deleted model '{model_name}': {models_deleted} model and {motions_deleted} motions")
                return {
                    'model_name': model_name,
                    'models_deleted': models_deleted,
                    'motions_deleted': motions_deleted
                }
        except Exception as e:
            self.logger.error(f"Error deleting model '{model_name}': {e}")
            raise e

    def get_database_stats(self):
        """Get comprehensive database statistics."""
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                stats = {}
                # Count total models
                cursor.execute("SELECT COUNT(*) FROM live2d_models")
                stats['total_models'] = cursor.fetchone()[0]
                # Count total motions
                cursor.execute("SELECT COUNT(*) FROM live2d_motions")
                stats['total_motions'] = cursor.fetchone()[0]
                # Get motions per model
                cursor.execute("""
                    SELECT m.model_name, COUNT(lm.id) as motion_count
                    FROM live2d_models m
                    LEFT JOIN live2d_motions lm ON m.id = lm.model_id
                    GROUP BY m.id, m.model_name
                    ORDER BY motion_count DESC
                """)
                stats['motions_per_model'] = [dict(row) for row in cursor.fetchall()]
                # Get motion types distribution
                cursor.execute("""
                    SELECT motion_type, COUNT(*) as count
                    FROM live2d_motions
                    GROUP BY motion_type
                    ORDER BY count DESC
                """)
                stats['motion_types'] = [dict(row) for row in cursor.fetchall()]
                return stats
        except Exception as e:
            self.logger.error(f"Error getting database stats: {e}")
            raise e

    def get_model_preview(self, model_name: str) -> Optional[str]:
        """
        Get the preview image data for a model.
        Returns the base64 image data or None if no preview exists.
        """
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT preview_image FROM live2d_models WHERE model_name = ?",
                    (model_name,)
                )
                result = cursor.fetchone()
                return result['preview_image'] if result else None
        except Exception as e:
            self.logger.error(f"Error getting preview for model {model_name}: {e}")
            return None

    def save_model_preview(self, model_name: str, preview_data: str) -> bool:
        """
        Save preview image data for a model.
        preview_data should be a base64 data URL or object URL.
        Returns True if successful, False otherwise.
        """
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE live2d_models SET preview_image = ?, last_updated = CURRENT_TIMESTAMP WHERE model_name = ?",
                    (preview_data, model_name)
                )
                conn.commit()
                success = cursor.rowcount > 0
                if success:
                    self.logger.info(f"Saved preview image for model: {model_name}")
                else:
                    self.logger.warning(f"No model found to update preview for: {model_name}")
                return success
        except Exception as e:
            self.logger.error(f"Error saving preview for model {model_name}: {e}")
            return False

    def has_model_preview(self, model_name: str) -> bool:
        """
        Check if a model has a cached preview image.
        Returns True if preview exists, False otherwise.
        """
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT preview_image FROM live2d_models WHERE model_name = ? AND preview_image IS NOT NULL AND preview_image != ''",
                    (model_name,)
                )
                return cursor.fetchone() is not None
        except Exception as e:
            self.logger.error(f"Error checking preview for model {model_name}: {e}")
            return False

    def clear_model_preview(self, model_name: str) -> bool:
        """
        Clear the cached preview image for a model.
        Returns True if successful, False otherwise.
        """
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE live2d_models SET preview_image = NULL, last_updated = CURRENT_TIMESTAMP WHERE model_name = ?",
                    (model_name,)
                )
                conn.commit()
                success = cursor.rowcount > 0
                if success:
                    self.logger.info(f"Cleared preview image for model: {model_name}")
                return success
        except Exception as e:
            self.logger.error(f"Error clearing preview for model {model_name}: {e}")
            return False

    def get_model_info(self, model_name: str) -> Optional[Dict]:
        """
        Get comprehensive model information for chat system integration.
        Returns model data with motions and expressions organized for chat use.
        """
        try:
            with get_live2d_connection() as conn:
                cursor = conn.cursor()
                
                # Get basic model info
                cursor.execute("""
                    SELECT id, model_name, model_path, config_file, description, preview_image
                    FROM live2d_models 
                    WHERE model_name = ?
                """, (model_name,))
                
                model_row = cursor.fetchone()
                if not model_row:
                    return None
                
                model_data = dict(model_row)
                
                # Get all motions for this model
                cursor.execute("""
                    SELECT motion_group, motion_index, motion_name, motion_type, file_path
                    FROM live2d_motions lm
                    WHERE lm.model_id = ?
                    ORDER BY lm.motion_group, lm.motion_index
                """, (model_data['id'],))
                
                motion_rows = cursor.fetchall()
                
                # Organize motions by group and type
                motions = {}
                expressions = {}
                
                for motion_row in motion_rows:
                    motion_data = dict(motion_row)
                    group = motion_data['motion_group']
                    motion_name = motion_data['motion_name']
                    motion_type = motion_data['motion_type']
                    
                    if motion_type == 'expression':
                        expressions[motion_name] = motion_data
                    else:
                        if group not in motions:
                            motions[group] = {}
                        motions[group][motion_name] = motion_data
                
                # Build comprehensive info for chat system
                return {
                    'model_name': model_data['model_name'],
                    'model_path': model_data['model_path'],
                    'config_file': model_data['config_file'],
                    'description': model_data.get('description', ''),
                    'preview_image': model_data.get('preview_image'),
                    'motions': motions,
                    'expressions': expressions,
                    'motion_count': len(motion_rows),
                    'available_motion_groups': list(motions.keys()),
                    'available_expressions': list(expressions.keys())
                }
                
        except Exception as e:
            self.logger.error(f"Error getting model info for {model_name}: {e}")
            return None

    def close(self):
        """Close database connection - No-op since we use context managers."""
        pass  # Context managers handle connection cleanup automatically

# Global instance for backward compatibility and external use
_live2d_manager = None

def get_live2d_manager():
    """Get the global Live2D manager instance."""
    global _live2d_manager
    if _live2d_manager is None:
        _live2d_manager = Live2DModelManager()
    return _live2d_manager

def get_model_info(model_name: str) -> Optional[Dict]:
    """Convenience function to get model info for chat system."""
    manager = get_live2d_manager()
    return manager.get_model_info(model_name)

def get_all_models() -> List[Dict]:
    """Convenience function to get all models."""
    manager = get_live2d_manager()
    return manager.get_all_models()
