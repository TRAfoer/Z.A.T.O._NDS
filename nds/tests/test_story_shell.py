from pathlib import Path
import subprocess,tempfile,unittest,shutil
ROOT=Path(__file__).resolve().parents[2]
class StoryShellTests(unittest.TestCase):
    def test_real_container_cold_reload_and_unsafe_save(self):
        with tempfile.TemporaryDirectory() as temp:
            exe=Path(temp)/'shell.exe'
            r=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_shell.c'),str(ROOT/'nds/source/story_save.c'),str(ROOT/'nds/tests/validate_story_shell.c'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(exe),str(Path(temp)/'game.sav')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertEqual((Path(temp)/'game.sav').stat().st_size,524288)
            cold=subprocess.run([str(exe),str(Path(temp)/'game.sav'),'--check-chapter'],capture_output=True,text=True)
            self.assertEqual(cold.returncode,0,cold.stdout+cold.stderr)
            print(r.stdout.strip());print(cold.stdout.strip())
if __name__=='__main__':unittest.main()
