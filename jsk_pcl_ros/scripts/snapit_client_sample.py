#!/usr/bin/env python

import rospy
import roslib

roslib.load_manifest("jsk_pcl_ros")
roslib.load_manifest("interactive_markers")
from interactive_markers.interactive_marker_server import *
from interactive_markers.menu_handler import *
from geometry_msgs.msg import *
from jsk_pcl_ros.srv import *
from jsk_pcl_ros.msg import *
from std_msgs.msg import *
import tf
import numpy

plane_center_pose = tf.transformations.identity_matrix()
FRAME_ID = "/camera_rgb_optical_frame"
tf_listener = None
snapit_result = None
def processFeedback(feedback):
  global plane_center_pose

  if feedback.event_type == InteractiveMarkerFeedback.BUTTON_CLICK:
    return
  elif feedback.event_type == InteractiveMarkerFeedback.MENU_SELECT:
    # snapit!!
    trans = snapit_result.transformation
    trans_matrix = tf.transformations.quaternion_matrix(numpy.array((trans.orientation.x,
                                                                     trans.orientation.y,
                                                                     trans.orientation.z,
                                                                     trans.orientation.w)))
    pos = numpy.array((trans.position.x, trans.position.y, trans.position.z, 1.0))
    new_pos = pos
    trans_matrix[0, 3] = new_pos[0]
    trans_matrix[1, 3] = new_pos[1]
    trans_matrix[2, 3] = new_pos[2]
    new_trans = numpy.dot(trans_matrix, plane_center_pose)
    
    new_pose = Pose()
    new_pose.position.x = new_trans[0, 3]
    new_pose.position.y = new_trans[1, 3]
    new_pose.position.z = new_trans[2, 3]
    q = tf.transformations.quaternion_from_matrix(new_trans)
    new_pose.orientation.x = q[0]
    new_pose.orientation.y = q[1]
    new_pose.orientation.z = q[2]
    new_pose.orientation.w = q[3]
    print "current"
    print plane_center_pose
    print "trans"
    print trans
    print "new_pose"
    print new_pose
    server.setPose("snapit", new_pose, Header(frame_id = FRAME_ID,
                                                   stamp = rospy.Time.now()))
    server.applyChanges()
    plane_center_pose = new_trans
    return
  elif feedback.event_type == InteractiveMarkerFeedback.POSE_UPDATE:
    pose = PoseStamped()
    pose.pose = feedback.pose
    pose.header = feedback.header
    transformed_pose = tf_listener.transformPose(FRAME_ID, pose)
    new_pose = tf.transformations.quaternion_matrix(numpy.array((
      transformed_pose.pose.orientation.x,
      transformed_pose.pose.orientation.y,
      transformed_pose.pose.orientation.z,
      transformed_pose.pose.orientation.w)))
    new_pose[0, 3] = transformed_pose.pose.position.x
    new_pose[1, 3] = transformed_pose.pose.position.y
    new_pose[2, 3] = transformed_pose.pose.position.z
    plane_center_pose = new_pose

def main():
  global tf_listener, snapit_result, server
  SIZE = 0.1
  rospy.init_node("snapit_sample")
  rospy.wait_for_service("snapit")
  tf_listener = tf.TransformListener()
  s = rospy.ServiceProxy("snapit", CallSnapIt)
  plane_pub = rospy.Publisher("target_plane", PolygonStamped)
  server = InteractiveMarkerServer("snapit_plane")
  

  int_marker = InteractiveMarker()
  int_marker.header.frame_id = "/camera_rgb_optical_frame"
  int_marker.pose.position.y = 0
  int_marker.scale = 1

  int_marker.name = "snapit"
  
  marker = Marker()
  marker.type = Marker.CUBE
  marker.scale.x = SIZE * 2
  marker.scale.y = SIZE * 2
  marker.scale.z = 0.001
  marker.color.g = 1.0
  marker.color.a = 0.5
  control = InteractiveMarkerControl()
  control.always_visible = True
  control.markers.append( marker)
  int_marker.controls.append( control )
                  
  control = InteractiveMarkerControl()
  control.orientation.w = 1
  control.orientation.x = 1
  control.orientation.y = 0
  control.orientation.z = 0
  control.name = "rotate_x"
  control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
  int_marker.controls.append(control)

  control = InteractiveMarkerControl()
  control.orientation.w = 1
  control.orientation.x = 1
  control.orientation.y = 0
  control.orientation.z = 0
  control.name = "move_x"
  control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
  int_marker.controls.append(control)
  control = InteractiveMarkerControl()
  control.orientation.w = 1
  control.orientation.x = 0
  control.orientation.y = 1
  control.orientation.z = 0
  control.name = "rotate_z"
  control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
  int_marker.controls.append(control)
  control = InteractiveMarkerControl()
  control.orientation.w = 1
  control.orientation.x = 0
  control.orientation.y = 1
  control.orientation.z = 0
  control.name = "move_z"
  control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
  int_marker.controls.append(control)
  control = InteractiveMarkerControl()
  control.orientation.w = 1
  control.orientation.x = 0
  control.orientation.y = 0
  control.orientation.z = 1
  control.name = "rotate_y"
  control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
  int_marker.controls.append(control)
  control = InteractiveMarkerControl()
  control.orientation.w = 1
  control.orientation.x = 0
  control.orientation.y = 0
  control.orientation.z = 1
  control.name = "move_y"
  control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
  int_marker.controls.append(control)
  server.insert(int_marker, processFeedback)

  menu_handler = MenuHandler()
  
  menu_handler.insert("SnapIt", callback=processFeedback )
  menu_handler.apply( server, int_marker.name )
  server.applyChanges()
  
  # call snapit
  rospy.sleep(2)
  while not rospy.is_shutdown():
    req = SnapItRequest()
    req.header.stamp = rospy.Time.now()
    req.header.frame_id = "/camera_rgb_optical_frame"
    plane = PolygonStamped()
    plane.header.stamp = rospy.Time.now()
    plane.header.frame_id = "/camera_rgb_optical_frame"
    points = [numpy.array((SIZE, SIZE, 0.0, 1.0)),
              numpy.array((-SIZE, SIZE, 0.0, 1.0)),
              numpy.array((-SIZE, -SIZE, 0.0, 1.0)),
              numpy.array((SIZE, -SIZE, 0.0, 1.0))]
    for p in points:
      plane.polygon.points.append(Point32(*numpy.dot(plane_center_pose, p)[:3]))
    plane_pub.publish(plane)
    req.target_plane = plane
    try:
      snapit_result = s(req)
    except Exception, m:
      rospy.logerr("error in snapit")
      print m
    rospy.sleep(1)
  

if __name__ == "__main__":
  main()
