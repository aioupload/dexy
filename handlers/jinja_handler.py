from dexy.dexy_filter import DexyFilter
from jinja2 import Environment
from ordereddict import OrderedDict
import jinja2
import json
import os
import re
import urllib

class FilenameHandler(DexyFilter):
    """Generate random filenames to track provenance of data."""
    ALIASES = ['fn']

    def process_text(self, input_text):
        # TODO this should not match more than two dashes
        for m in re.finditer("dexy--(\S+)\.([a-z]+)", input_text):
            key = m.groups()[0]
            ext = m.groups()[1]
            key_with_ext = "%s.%s" % (key, ext)

            if key_with_ext in self.artifact.inputs().keys():
                artifact = self.artifact.inputs()[key_with_ext]
                self.artifact.log.debug("existing key %s in artifact %s links to file %s" %
                          (key, self.artifact.key, artifact.filename()))
            else:
                artifact = self.artifact.add_additional_artifact(key_with_ext, ext)
                self.artifact.log.debug("added key %s to artifact %s ; links to file %s" %
                          (key, self.artifact.key, artifact.filename()))

            input_text = input_text.replace(m.group(), artifact.filename())

        # Hack to replace __ with -- in case we want to document how to use this
        # filter, we can't use -- because this will be acted upon.
        for m in re.finditer("dexy__(.+)\.([a-z]+)", input_text):
            key = m.groups()[0]
            ext = m.groups()[1]
            replacement_key = "dexy--%s.%s" % (key, ext)
            input_text = input_text.replace(m.group(), replacement_key)

        return input_text

class JinjaHandler(DexyFilter):
    """
    Runs the Jinja templating engine on your document to incorporate dynamic
    content.
    """
    ALIASES = ['jinja']
    FINAL = True

    def pre_and_clippy(self, text):
        return """<pre>
%s
</pre>%s
""" % (text, self.clippy_helper(text))

    def clippy_helper(self, text):
        quoted_text = urllib.quote(text)
        return """<object classid="clsid:d27cdb6e-ae6d-11cf-96b8-444553540000"
                width="110"
                height="14"
                id="clippy" >
        <param name="movie" value="/clippy.swf"/>
        <param name="allowScriptAccess" value="always" />
        <param name="quality" value="high" />
        <param name="scale" value="noscale" />
        <param NAME="FlashVars" value="text=%s">
        <param name="bgcolor" value="#ffffff">
        <embed src="/clippy.swf"
               width="110"
               height="14"
               name="clippy"
               quality="high"
               allowScriptAccess="always"
               type="application/x-shockwave-flash"
               pluginspage="http://www.macromedia.com/go/getflashplayer"
               FlashVars="text=%s"
               bgcolor="#ffffff"
        />
        </object>""" % (quoted_text, quoted_text)

    def process_text(self, input_text):
        document_data = {}
        raw_data = {}
        local_inputs = {}

        notextile = self.artifact.args.has_key('notextile') and self.artifact.args['notextile']

        for key, a in self.artifact.inputs().items():
            if a.is_output_cached():
                self.artifact.log.debug("Loading artifact %s" % key)
                a.load() # reload

            # Full path keys
            keys = [key, a.canonical_filename()]

            # Shortcut keys if in common directory
            if os.path.dirname(self.artifact.name) == os.path.dirname(key) or not os.path.dirname(key):
                keys.append(os.path.basename(key))
                fn = a.canonical_filename()
                keys.append(os.path.basename(fn))
                keys.append(os.path.splitext(fn)[0]) # TODO deal with collisions
                if os.path.dirname(key):
                    local_inputs[key] = a

            # Do special handling of data
            if a.ext == '.json':
                path_to_file = os.path.join(self.artifact.artifacts_dir, a.filename())
#                if not os.path.exists(path_to_file):
#                    a.state = 'complete'
#                    a.save()
                # TODO read this from memory rather than loading from file?
                # TODO capture load errors
                unsorted_json = json.load(open(path_to_file, "r"))

                def sort_dict(d):
                    od = OrderedDict()
                    for k in sorted(d.keys()):
                        v = d[k]
                        if isinstance(v, dict) or isinstance(v, OrderedDict):
                            od[k] = sort_dict(v)
                        else:
                            if notextile and isinstance(v, str) and "<span" in v and not "<notextile>" in v:
                                od[k] = "<notextile>\n%s\n</notextile>" % v
                            else:
                                od[k] = v
                    return od

                if hasattr(unsorted_json, 'keys'):
                    data = sort_dict(unsorted_json)
                else:
                    data = unsorted_json

            elif notextile and a.ext == '.html':
                data = OrderedDict()
                for s, h in a.data_dict.items():
                    if "<notextile>" in h:
                        data[s] = h
                    else:
                        data[s] = "<notextile>\n%s\n</notextile>" % h
            else:
                data = a.data_dict

            for k in keys:
                raw_data[k] = a.output_text()
                if data.keys() == ['1']:
                    document_data[k] = data['1']
                else:
                    document_data[k] = data

        if self.artifact.ext == ".tex":
            self.artifact.log.debug("changing jinja tags to << >> etc. for %s" % self.artifact.key)
            env = Environment(
                block_start_string = '<%',
                block_end_string = '%>',
                variable_start_string = '<<',
                variable_end_string = '>>',
                comment_start_string = '<#',
                comment_end_string = '#>'
                )
        else:
            env = Environment()

        template_hash = {
            's' : self.artifact,
            'f' : self,
            'a' : self.artifact._inputs,
            'ak' : sorted(self.artifact._inputs.keys()),
            'd' : document_data,
            'dk' : sorted(document_data.keys()),
            'l' : local_inputs,
            'lk' : sorted(local_inputs.keys()),
            'r' : raw_data
        }

        if self.artifact.args.has_key('jinjavars'):
            for k, v in self.artifact.args['jinjavars'].items():
                if template_hash.has_key(k):
                    raise Exception("Please do not set a jinjavar for %s as this conflicts with standard vars: %s" % (k, ', '.join(template_hash.keys())))
                template_hash[k] = v

        try:
            template = env.from_string(input_text)
            result = str(template.render(template_hash))
        except jinja2.exceptions.TemplateSyntaxError as e:
            print "jinja error occurred processing line", e.lineno
            print self.artifact.previous_artifact_filename
            raise e
        except jinja2.exceptions.UndefinedError as e:
            print "jinja UndefinedError error occurred"
            print self.artifact.previous_artifact_filename
            raise e
        except Exception as e:
            print e.__class__.__name__
            raise e

        return result
